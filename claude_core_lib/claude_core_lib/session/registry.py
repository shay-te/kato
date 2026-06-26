"""Read Claude Code's live-process session registry (``~/.claude/sessions``).

Every running Claude CLI writes ``<pid>.json`` there at startup —
``{"pid": ..., "sessionId": ..., "cwd": ..., "version": ...}`` — and
removes it on clean exit. A registry entry whose pid is still alive
means that session id is actively held by a running CLI process.

Why this matters: resuming a session that another live CLI process still
holds makes ``claude --resume <id>`` silently start a FRESH session
under a NEW id — a conversation that looks resumed but remembers
nothing. On Windows this happened constantly: ``claude`` resolves to
the npm ``claude.cmd`` shim, so the caller's subprocess handle was a cmd.exe
wrapper and ``TerminateProcess`` (what ``Popen.kill``/``SIGTERM`` mean
on Windows) killed only the wrapper. The real node-based CLI survived
as an orphan, kept appending to the transcript, and every subsequent
``--resume`` for that task produced a memoryless session.

This module is the pre-spawn guard's toolbox: find live holders of a
session id, wait briefly for them to let go, and — when they are
provably leftover CLI processes — kill their process tree so the
resume can actually resume.

Safety: killing by pid is gated on the process image name (Windows)
matching a known CLI process (``node.exe`` / ``claude.exe`` / the
cmd shim), so a recycled pid belonging to an unrelated program is
never touched. Everything is best-effort and never raises — a failed
release degrades back to today's behaviour, it must not block a spawn.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from agent_core_lib.agent_core_lib.helpers.session_id_utils import (
    fix_session_id,
    same_session_id,
)


_IS_WINDOWS = os.name == 'nt'

# Image names a registry pid may legitimately have. The registry is
# written by the CLI itself, so a live holder is node (npm install),
# the native single-binary build, or the cmd.exe shim wrapping either.
# A pid whose image is anything else means the pid was recycled by the
# OS for an unrelated program — never kill those.
KNOWN_CLI_IMAGE_NAMES = frozenset({
    'node.exe', 'claude.exe', 'cmd.exe', 'node', 'claude',
})


def default_registry_dir() -> Path:
    """Claude Code's live-session registry directory."""
    return Path.home() / '.claude' / 'sessions'


def live_session_holders(
    agent_session_id: str,
    *,
    registry_dir: Path | str | None = None,
    pid_alive=None,
) -> list[dict]:
    """Registry entries for live processes holding ``agent_session_id``.

    Dead pids are filtered out (a crashed CLI leaves its ``<pid>.json``
    behind; that's stale bookkeeping, not a holder). Malformed entries
    are skipped — the registry belongs to the CLI and the caller must not
    crash on whatever it finds there.
    """
    session_id = fix_session_id(agent_session_id)
    if not session_id:
        return []
    root = Path(registry_dir) if registry_dir else default_registry_dir()
    if not root.is_dir():
        return []
    alive = pid_alive or _pid_alive
    holders: list[dict] = []
    for path in root.glob('*.json'):
        entry = _read_registry_entry(path)
        if entry is None:
            continue
        if not same_session_id(entry.get('sessionId'), session_id):
            continue
        pid = _coerce_pid(entry.get('pid'))
        if pid is None:
            continue
        try:
            if not alive(pid):
                continue
        except Exception:
            continue
        entry['pid'] = pid
        holders.append(entry)
    return holders


def release_session_holders(
    agent_session_id: str,
    *,
    wait_seconds: float = 8.0,
    poll_interval_seconds: float = 0.25,
    logger=None,
    registry_dir: Path | str | None = None,
    pid_alive=None,
    kill_tree=None,
    image_name=None,
    clock=time.monotonic,
    sleep=time.sleep,
) -> bool:
    """Make sure no live CLI process holds ``agent_session_id`` anymore.

    Strategy: wait up to ``wait_seconds`` for the holder to exit on its
    own (the common case — the caller just closed its stdin and the CLI is
    finishing its in-flight turn), then force-kill whatever is left,
    provided its image name proves it is a CLI process and not a
    recycled pid. Returns ``True`` when the session is free, ``False``
    when a holder survived (the caller logs and proceeds — the spawn
    guard downstream still refuses the memoryless impostor).

    No holders is the fast path: one directory scan, no waiting.
    """
    session_id = fix_session_id(agent_session_id)
    if not session_id:
        return True

    def find() -> list[dict]:
        return live_session_holders(
            session_id, registry_dir=registry_dir, pid_alive=pid_alive,
        )

    holders = find()
    if not holders:
        return True
    pids = sorted(entry['pid'] for entry in holders)
    if logger is not None:
        logger.warning(
            'session %s is still held by live claude process(es) %s — '
            'waiting up to %.0fs for release before --resume (a held '
            'session cannot be resumed; claude would silently start a '
            'blank conversation)',
            session_id, pids, wait_seconds,
        )
    deadline = clock() + max(0.0, float(wait_seconds))
    while clock() < deadline:
        sleep(max(0.05, float(poll_interval_seconds)))
        holders = find()
        if not holders:
            if logger is not None:
                logger.info(
                    'session %s released by its previous process(es); '
                    'safe to --resume', session_id,
                )
            return True
    kill = kill_tree or kill_process_tree
    name_of = image_name or _image_name
    for entry in holders:
        pid = entry['pid']
        image = ''
        try:
            image = str(name_of(pid) or '')
        except Exception:
            image = ''
        if image and image.lower() not in KNOWN_CLI_IMAGE_NAMES:
            # The pid was recycled for an unrelated program — never
            # kill it. (The registry entry is stale; the CLI that
            # wrote it is gone, so the session is effectively free.)
            if logger is not None:
                logger.warning(
                    'session %s registry pid %s now belongs to %r; '
                    'skipping kill (stale registry entry)',
                    session_id, pid, image,
                )
            continue
        if logger is not None:
            logger.warning(
                'session %s still held by pid %s (%s) after %.0fs; '
                'killing the leftover CLI process tree so --resume can '
                'actually resume',
                session_id, pid, image or 'unknown image', wait_seconds,
            )
        try:
            kill(pid, logger=logger)
        except Exception:
            if logger is not None:
                logger.exception(
                    'failed to kill leftover claude process %s for '
                    'session %s', pid, session_id,
                )
    # Give the OS a moment to reap, then report the ground truth.
    sleep(max(0.05, float(poll_interval_seconds)))
    return not find()


def kill_process_tree(pid: int, *, logger=None) -> bool:
    """Force-kill ``pid`` AND its children. True when the kill landed.

    Windows needs the tree semantics explicitly: ``TerminateProcess``
    (which is all ``Popen.kill``/``send_signal(SIGTERM)`` can do there)
    kills exactly one process, and the npm ``claude.cmd`` shim makes
    the real CLI a *child* of the process the caller holds — killing the
    wrapper orphans it. ``taskkill /T`` walks the child tree.

    POSIX has no wrapper problem (the shim is a shebang script, so the
    spawned process IS the CLI) — a plain SIGKILL suffices.
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if _IS_WINDOWS:
        try:
            completed = subprocess.run(
                ['taskkill', '/T', '/F', '/PID', str(pid)],
                capture_output=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            if logger is not None:
                logger.exception('taskkill /T /F /PID %s failed to run', pid)
            return False
        # 128 = "process not found" — already dead counts as success.
        return completed.returncode in (0, 128)
    import signal
    # ``SIGKILL`` does not exist on Windows; the getattr keeps this
    # branch importable there (tests patch ``_IS_WINDOWS`` to exercise
    # both paths on one platform).
    sigkill = getattr(signal, 'SIGKILL', signal.SIGTERM)
    try:
        os.kill(pid, sigkill)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return True


# ----- internals -----


def _read_registry_entry(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _coerce_pid(value) -> int | None:
    try:
        pid = int(value)
    except (TypeError, ValueError):
        return None
    return pid if pid > 0 else None


def _pid_alive(pid: int) -> bool:
    """Is ``pid`` a currently-running process?

    Windows must NOT use ``os.kill(pid, 0)`` — on Windows that calls
    ``TerminateProcess`` with exit code 0, i.e. it would KILL the
    process we only meant to probe. Query the exit code instead.
    """
    if _IS_WINDOWS:
        return _pid_alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _pid_alive_windows(pid: int) -> bool:
    import ctypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid),
    )
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _image_name(pid: int) -> str:
    """Executable image name for ``pid`` ('' when unknown).

    Used as the don't-kill-recycled-pids gate. Windows asks tasklist;
    Linux reads ``/proc/<pid>/comm``; anywhere else returns '' (the
    caller treats unknown as killable — on POSIX the registry pid was
    written by the CLI itself moments ago, and pid recycling within a
    session's lifetime is not a realistic Windows-style hazard there).
    """
    if _IS_WINDOWS:
        try:
            completed = subprocess.run(
                ['tasklist', '/FI', f'PID eq {int(pid)}', '/FO', 'CSV', '/NH'],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired, ValueError):
            return ''
        first_line = (completed.stdout or '').strip().splitlines()
        if not first_line:
            return ''
        first_field = first_line[0].split('","')[0].strip('"')
        # "INFO: No tasks are running..." lands here when the pid is
        # gone; a real row's first CSV field is the image name.
        if not first_field.lower().endswith('.exe'):
            return ''
        return first_field
    try:
        comm = Path(f'/proc/{int(pid)}/comm').read_text(encoding='utf-8')
    except (OSError, ValueError, UnicodeDecodeError):
        return ''
    return comm.strip()
