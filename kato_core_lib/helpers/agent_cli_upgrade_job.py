"""Run the agent-CLI upgrade as a background job with live progress.

The upgrade used to be a single blocking POST: the browser sat on an open
request for the whole ``npm install`` (up to five minutes) with nothing but a
disabled button, and a reload or a dropped connection lost all trace of a
command that was still running on the host.

This runs it in a worker thread instead and exposes a snapshot the UI polls, so
the operator gets a progress bar, the live command output, and a result that
survives closing the modal or reloading the page.

**Progress honesty.** ``npm`` emits no percentage, so the bar is driven by
observed MILESTONES in the command output (each with a floor percent), plus a
bounded time-based creep between them so the bar always moves without ever
overtaking the next milestone. It reaches 100 only after the new binary has
been re-probed and its version read back — never on "the process exited".

One upgrade runs at a time per host: two concurrent ``npm install -g`` calls on
the same prefix corrupt each other.
"""

from __future__ import annotations

import math
import re
import subprocess
import threading
import time

from kato_core_lib.helpers.agent_version_utils import (
    UPGRADE_SUCCESS_MESSAGE,
    installed_version,
    reset_latest_version_cache,
    upgrade_plan,
)

# How much command output to keep for the UI's log tail. Enough to diagnose a
# failure (npm's EACCES advice runs long) without unbounded memory on a job
# that streams for minutes.
_MAX_LINES = 300
_TIMEOUT_SECONDS = 600
# How fast the between-milestone creep approaches the next floor. ~12s means a
# typical 20-40s npm install looks continuously busy without the bar parking.
_CREEP_TAU_SECONDS = 12.0

# (matcher, floor percent, step label) — the first match at or above the
# current floor advances the bar. Ordered by floor. Patterns cover BOTH
# managers: npm's own log lines and the claude CLI's self-updater.
_MILESTONES = (
    (re.compile(r'npm (http|verb|sill) fetch|GET 200|registry\.npmjs|fetching', re.I),
     30, 'Downloading…'),
    (re.compile(r'\bextract|\bunpack|reify|linking|installing', re.I),
     60, 'Installing…'),
    (re.compile(r'\b(added|changed|updated|removed)\b[^\n]*\bpackages?\b|installed', re.I),
     85, 'Finishing install…'),
)
# The bar never creeps past this before the process actually exits — the last
# stretch is reserved for the post-install version verification.
_RUNNING_CEILING = 92
_VERIFY_PERCENT = 95


def _blank_state() -> dict:
    return {
        'state': 'idle',      # idle | running | done | error
        'percent': 0,
        'step': '',
        'command': '',
        'manager': '',
        'lines': [],
        'ok': None,
        'message': '',
        'version_before': None,
        'version_after': None,
        'started_at': None,
        'finished_at': None,
    }


_state: dict = _blank_state()
_lock = threading.Lock()
_thread: threading.Thread | None = None


def _public(state: dict) -> dict:
    """The wire shape — internal progress bookkeeping (``_floor``…) stripped."""
    snapshot = {k: v for k, v in state.items() if not k.startswith('_')}
    snapshot['lines'] = list(state.get('lines') or [])
    return snapshot


def status() -> dict:
    """A snapshot of the current/last upgrade. Safe to poll. Never raises."""
    with _lock:
        snapshot = _public(_state)
    if snapshot['state'] == 'running':
        # Recompute the creep on read so the bar advances between output lines
        # (npm can go quiet for many seconds mid-download).
        snapshot['percent'] = _crept_percent(snapshot)
    return snapshot


def reset() -> None:
    """Clear job state (tests, and re-arming after a finished run)."""
    global _state, _thread
    with _lock:
        _state = _blank_state()
        _thread = None


def is_running() -> bool:
    with _lock:
        return _state['state'] == 'running'


def start(env: dict | None = None, spawner=None, verifier=None) -> dict:
    """Begin an upgrade in the background; return the initial snapshot.

    Refuses (without disturbing the running job) when one is already in flight,
    and refuses when ``upgrade_plan`` doesn't allow an in-app upgrade here.
    ``spawner``/``verifier`` are injectable for tests.
    """
    global _thread
    with _lock:
        if _state['state'] == 'running':
            snapshot = _public(_state)
            snapshot['already_running'] = True
            return snapshot

    plan = upgrade_plan(env)
    if not plan['allowed']:
        with _lock:
            _state.update(_blank_state(), state='error', ok=False,
                          message=plan['reason'], finished_at=time.time())
            return _public(_state)

    before = installed_version(env)
    with _lock:
        _state.update(
            _blank_state(),
            state='running', percent=3, step='Starting…',
            command=plan['command'], manager=plan['manager'],
            version_before=before, started_at=time.time(),
        )
        _state['_floor'] = 3
        _state['_floor_at'] = time.time()
        _thread = threading.Thread(
            target=_run, args=(plan, env, spawner, verifier),
            name='agent-cli-upgrade', daemon=True,
        )
        _thread.start()
        return _public(_state)


def _run(plan: dict, env, spawner, verifier) -> None:
    """Worker body — stream the command, then verify the installed version."""
    spawn = spawner or _default_spawner
    try:
        code, timed_out = _stream_command(plan['argv'], spawn)
    except Exception as exc:  # spawn failure (missing binary, permissions…)
        _finish(ok=False, message=f'upgrade failed to run: {exc}')
        return
    if timed_out:
        _finish(ok=False,
                message=f"{plan['manager']} timed out after {_TIMEOUT_SECONDS}s")
        return
    if code != 0:
        _finish(ok=False, message=f"{plan['manager']} exited with code {code}")
        return

    _set(percent=_VERIFY_PERCENT, step='Verifying new version…')
    reset_latest_version_cache()
    try:
        after = (verifier or installed_version)(env)
    except Exception:
        after = None
    with _lock:
        _state['version_after'] = after
        before = _state['version_before']
    if after is None:
        # The command succeeded but the binary won't report a version — the
        # operator must not be told "upgraded" on that.
        _finish(ok=False, message=(
            'the upgrade command succeeded but the CLI did not report a '
            'version afterwards — check the output below'
        ))
        return
    changed = f' ({before} → {after})' if before and before != after else f' ({after})'
    _finish(ok=True, message=UPGRADE_SUCCESS_MESSAGE + changed)


def _stream_command(argv: list, spawn) -> tuple[int, bool]:
    """Run ``argv``, feeding each output line into the progress state.

    Returns ``(returncode, timed_out)``. stderr is merged into stdout so npm's
    warnings and errors land in the same tail the operator reads.

    The timeout is enforced by a WATCHDOG rather than a check inside the read
    loop: ``for line in stream`` blocks, so a command that hangs while printing
    nothing — the exact shape of a wedged registry fetch — would never reach a
    deadline test and could run forever. Killing the process from a timer
    closes the stream, which unblocks the loop.
    """
    process = spawn(argv)
    timer = threading.Timer(_TIMEOUT_SECONDS, _kill_quietly, args=(process,))
    timer.daemon = True
    timer.start()
    started = time.monotonic()
    try:
        stream = process.stdout
        if stream is not None:
            for line in stream:
                _record_line(line.rstrip('\n'))
        try:
            code = process.wait(timeout=30)
        except Exception:
            _kill_quietly(process)
            code = -1
    finally:
        timer.cancel()
    # The watchdog fires silently, so infer it from the elapsed time rather
    # than from a flag the killed process can't set.
    timed_out = (time.monotonic() - started) >= _TIMEOUT_SECONDS
    return code, timed_out


def _kill_quietly(process) -> None:
    try:
        process.kill()
    except Exception:
        pass


def _default_spawner(argv: list):
    return subprocess.Popen(
        argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding='utf-8', errors='replace', bufsize=1,
    )


def _record_line(line: str) -> None:
    """Append to the log tail and advance the bar if the line is a milestone."""
    with _lock:
        if _state['state'] != 'running':
            return
        if line:
            _state['lines'].append(line)
            del _state['lines'][:-_MAX_LINES]
        for matcher, floor, label in _MILESTONES:
            if floor > _state['_floor'] and matcher.search(line):
                _state['_floor'] = floor
                _state['_floor_at'] = time.time()
                _state['percent'] = floor
                _state['step'] = label


def _crept_percent(snapshot: dict) -> int:
    """Milestone floor + a bounded, decelerating creep toward the next floor.

    Asymptotic so the bar keeps moving during npm's long quiet stretches but
    can never reach — let alone pass — the next milestone it hasn't observed.
    """
    with _lock:
        floor = _state.get('_floor', 0)
        floor_at = _state.get('_floor_at') or time.time()
    ceiling = next(
        (m[1] for m in _MILESTONES if m[1] > floor), _RUNNING_CEILING,
    )
    ceiling = min(ceiling, _RUNNING_CEILING)
    if ceiling <= floor:
        return int(max(snapshot['percent'], floor))
    elapsed = max(0.0, time.time() - floor_at)
    crept = floor + (ceiling - floor) * (1 - math.exp(-elapsed / _CREEP_TAU_SECONDS))
    return int(max(snapshot['percent'], min(crept, ceiling - 1)))


def _set(**fields) -> None:
    with _lock:
        _state.update(fields)


def _finish(ok: bool, message: str) -> None:
    with _lock:
        _state.update(
            state='done' if ok else 'error',
            ok=ok,
            message=message,
            percent=100 if ok else _state['percent'],
            step='Done' if ok else 'Failed',
            finished_at=time.time(),
        )
