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
import time
from pathlib import Path

from agent_core_lib.agent_core_lib.helpers.process_liveness import (
    coerce_pid as _coerce_pid,
    image_name as _image_name,
    kill_process_tree as _kill_process_tree,
    pid_alive as _pid_alive,
)
from agent_core_lib.agent_core_lib.helpers.session_id_utils import (
    fix_session_id,
    same_session_id,
)


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
    """Force-kill a leftover Claude process and its children.

    Claude's binding of the shared cross-platform kill: the tree semantics
    matter here because the npm ``claude.cmd`` shim makes the real CLI a child
    of the process the caller holds. See
    ``agent_core_lib.helpers.process_liveness``.
    """
    return _kill_process_tree(pid, logger=logger, label='claude')


# ----- internals -----


def _read_registry_entry(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload








