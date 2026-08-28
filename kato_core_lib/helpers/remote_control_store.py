"""The per-task Remote Control preference — where it is kept, and how it is applied.

Remote Control hands a live Claude session to claude.ai / the Claude app so
the operator can pick the same conversation up from their phone (see
``claude_core_lib/helpers/remote_control.py`` for the mechanism). The bridge
belongs to ONE subprocess and dies with it — and kato respawns chat sessions
constantly (a tab goes idle, the subprocess exits, the next message resumes
it). So the operator's choice has to live somewhere outside the subprocess,
or "on" would evaporate the first time they stopped typing.

That is all this file is: the set of task ids the operator has switched
Remote Control ON for. The webserver re-sends the toggle whenever it spawns a
session for one of them, and drops the id when the operator switches it off.

Stored at ``~/.kato/remote_control.json`` (override via
``KATO_REMOTE_CONTROL_PATH``) as a plain list of task ids, written verbatim —
the canonical platform id the UI sends, matching the in-memory override maps
the routes key by (which are NOT case-normalized).

Deliberately NOT a global "all sessions" switch, which is the shape Claude's
own ``remoteControlAtStartup`` setting takes. Kato runs many task sessions at
once and they are not interchangeable: exposing every one of them because the
operator wanted to follow a single task from their phone is a much bigger
statement than they made.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from utils_core_lib.utils_core_lib.atomic_write import atomic_write_json
from kato_core_lib.helpers.kato_paths_utils import kato_home_path

_ENV_KEY = 'KATO_REMOTE_CONTROL_PATH'
_FILENAME = 'remote_control.json'

# ``set_remote_control_enabled`` is read-modify-write against the whole file.
# Without this lock two tasks toggled at the same moment can both read the
# old set before either writes, silently dropping one. Mirrors
# ``plan_mode_store``.
_lock = threading.Lock()


def _path() -> Path:
    return kato_home_path(_FILENAME, env_key=_ENV_KEY)


def _norm(task_id: object) -> str:
    return str(task_id or '').strip()


def read_remote_control_tasks() -> set[str]:
    """Task ids with Remote Control switched on (empty when none/unreadable)."""
    path = _path()
    if not path.is_file():
        return set()
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return set()
    if not isinstance(data, list):
        return set()
    return {_norm(item) for item in data if _norm(item)}


def set_remote_control_enabled(task_id: str, on: bool) -> None:
    """Persist (``on``) or clear (``not on``) a task's Remote Control choice.

    Best-effort + idempotent: a blank task id is ignored, and writing the same
    state twice is a no-op.
    """
    task = _norm(task_id)
    if not task:
        return
    with _lock:
        tasks = read_remote_control_tasks()
        if (task in tasks) == bool(on):
            return
        if on:
            tasks.add(task)
        else:
            tasks.discard(task)
        path = _path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        atomic_write_json(path, sorted(tasks))


def is_remote_control_enabled(task_id: str) -> bool:
    """Whether the operator left Remote Control on for this task."""
    task = _norm(task_id)
    return bool(task) and task in read_remote_control_tasks()


def remote_control_session_name(task_id: str) -> str:
    """What the session is called in the Claude app's session list.

    That list is all the operator gets to choose from on the other device,
    and the CLI's own default names every session after the machine — which
    is the same string for all of them. The task id is the one label that
    tells a host's sessions apart. Product text, so it lives here rather
    than in the transport.
    """
    return f'kato {_norm(task_id)}'.strip()


def apply_remote_control(session, task_id: str, on: bool) -> dict:
    """Toggle Remote Control on one live session. Raises on failure.

    Returns the new state, or ``{}`` when there is nothing to toggle — no
    session, a dead one, or a backend with no such feature (Codex). Those
    are not errors: the preference is stored either way and the next Claude
    spawn applies it.
    """
    toggle = getattr(session, 'set_remote_control', None)
    if session is None or not callable(toggle):
        return {}
    if not getattr(session, 'is_alive', False):
        return {}
    return toggle(on, name=remote_control_session_name(task_id)) or {}


def schedule_remote_control_for_spawn(session, task_id: str, logger=None) -> None:
    """Re-bridge a freshly spawned session, off the spawning thread.

    The bridge belongs to ONE subprocess and dies with it, so a task the
    operator left switched on comes back disconnected on every respawn
    unless the toggle is re-sent — and kato respawns constantly (an idle
    tab, a comment run, a review fix).

    Fire-and-forget on its own thread: enabling is a network round trip
    through the CLI, and whoever triggered the spawn is waiting on their
    turn, not on this. Never raises — a bridge that fails to come back must
    not take the spawn down with it.
    """
    if not is_remote_control_enabled(task_id):
        return
    if not callable(getattr(session, 'set_remote_control', None)):
        return

    def _worker() -> None:
        try:
            apply_remote_control(session, task_id, True)
        except Exception as exc:
            if logger is not None:
                logger.warning(
                    'task %s: could not re-enable remote control after a '
                    'respawn: %s', task_id, exc,
                )

    threading.Thread(
        target=_worker,
        name=f'kato-remote-control-{_norm(task_id)}',
        daemon=True,
    ).start()
