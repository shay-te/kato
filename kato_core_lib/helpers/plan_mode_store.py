"""Persistent per-task agent-MODE lock.

The composer's plan-mode toggle locks a task's chat session to
``--permission-mode plan`` (planning only, never edits). Unlike the
model/effort tuning knobs — which are deliberately ephemeral — plan mode
is a SAFETY lock, so it must survive a restart: kato persists the set of
plan-locked task ids here and reloads them into the live override map at
boot (see ``create_app``), so the next session respawn re-applies the lock.

Stored at ``~/.kato/plan_mode.json`` (override via ``KATO_PLAN_MODE_PATH``).
Task ids are stored verbatim — the canonical platform id the UI sends — so
they reload straight into the in-memory override map the routes key by
(which is NOT case-normalized).

TWO on-disk shapes, both read:

* ``{task_id: "plan"|"default"|"bypassPermissions", ...}`` — current. The
  value is the literal ``--permission-mode`` the spawn passes, so the mode
  the operator picked in the composer survives a restart exactly.
* ``[task_id, ...]`` — what the plan-only toggle wrote before modes existed.
  Read as "every listed task is plan-locked" so an upgrade never silently
  drops a safety lock; the next write migrates the file to the mapping.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from utils_core_lib.utils_core_lib.atomic_write import atomic_write_json
from kato_core_lib.helpers.kato_paths_utils import kato_home_path

_ENV_KEY = 'KATO_PLAN_MODE_PATH'
_FILENAME = 'plan_mode.json'

# set_plan_mode() is read-modify-write against the whole file — without
# this lock, two tasks toggling plan mode around the same moment can both
# read the old set before either writes, silently reverting one task's
# SAFETY lock. Mirrors tool_decision_store.py's pattern.
_lock = threading.Lock()


def _path() -> Path:
    return kato_home_path(_FILENAME, env_key=_ENV_KEY)


def _norm(task_id: object) -> str:
    return str(task_id or '').strip()


#: The literal ``--permission-mode`` value for the planning lock.
PLAN_MODE = 'plan'

#: The setting naming the mode a task starts in when nobody has picked one
#: for it (Settings → Claude agent → Default mode for new tasks).
DEFAULT_MODE_ENV = 'KATO_CLAUDE_DEFAULT_MODE'
#: The composer's modes it may name — '' is "Edit automatically", kato's
#: built-in default.
DEFAULT_MODE_CHOICES = ('', 'default', 'explain', PLAN_MODE, 'bypassPermissions')
#: How an explicit "Edit automatically" pick is kept once a default mode is
#: set: as '' it would read back as that default.
EDIT_AUTOMATICALLY_MODE = 'acceptEdits'


def configured_default_task_mode() -> str:
    """The mode a task starts in when nobody picked one for it.

    Read live from the environment, which a Settings save mirrors into, so a
    change applies to the next spawn without a restart. An unknown value
    counts as unset: a typo must never become a ``--permission-mode`` that
    fails every spawn.
    """
    value = _norm(os.environ.get(DEFAULT_MODE_ENV))
    return value if value in DEFAULT_MODE_CHOICES else ''


def picked_mode_to_store(mode: object) -> str:
    """The value to keep for a mode the operator just picked.

    "Edit automatically" is '' — "no mode of its own" — which, once a default
    mode is set, would read back as THAT default: the pick would never stick.
    So it is kept as an explicit ``acceptEdits`` then; with no default set,
    '' as before.
    """
    value = _norm(mode)
    if not value and configured_default_task_mode():
        return EDIT_AUTOMATICALLY_MODE
    return value


def read_task_modes() -> dict[str, str]:
    """Task id → persisted ``--permission-mode`` (empty when none/unreadable).

    Accepts both on-disk shapes (see the module docstring); a legacy list is
    read as plan-locked so upgrading never quietly releases a safety lock.
    """
    path = _path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    if isinstance(data, list):
        return {_norm(item): PLAN_MODE for item in data if _norm(item)}
    if not isinstance(data, dict):
        return {}
    modes: dict[str, str] = {}
    for task_id, mode in data.items():
        task, value = _norm(task_id), _norm(mode)
        if task and value:
            modes[task] = value
    return modes


def read_plan_mode_tasks() -> set[str]:
    """The set of plan-locked task ids (empty when none / unreadable)."""
    return {
        task for task, mode in read_task_modes().items() if mode == PLAN_MODE
    }


def _write(modes: dict[str, str]) -> None:
    path = _path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    atomic_write_json(path, dict(sorted(modes.items())))


def set_task_mode(task_id: str, mode: object) -> None:
    """Persist a task's agent mode; an empty ``mode`` clears the override.

    Best-effort + idempotent: a blank task id is ignored, and writing the
    same state twice is harmless.
    """
    task = _norm(task_id)
    if not task:
        return
    value = _norm(mode)
    with _lock:
        modes = read_task_modes()
        if modes.get(task, '') == value:
            return
        if value:
            modes[task] = value
        else:
            modes.pop(task, None)
        _write(modes)


def set_plan_mode(task_id: str, on: bool) -> None:
    """Persist (``on``) or clear (``not on``) the plan-mode lock for a task."""
    set_task_mode(task_id, PLAN_MODE if on else '')


def task_permission_mode(task_id: str, default: str = '') -> str:
    """The operator's persisted ``--permission-mode`` for a task, else
    ``default``, else the configured default mode for new tasks
    (``configured_default_task_mode``) — '' = kato's built-in default.

    The single read-side entry point for "what mode did the operator lock
    this task to". It exists because the lock used to be reachable ONLY
    through the webserver's in-memory override map, so exactly one spawn
    site (the chat-send route) honoured it: autonomous implementation,
    review-comment fixes, and diff-comment respawns all started with the
    configured editing mode instead. A task the operator had locked to
    Plan would sit there and then edit files on the next scan tick —
    which is precisely what a safety lock must not do.

    Reads the file each call rather than caching: the operator can flip
    the mode at any moment from the composer, a stale cache would keep
    spawning under the OLD mode, and a lock that applies late is not a
    lock. The file is small and spawns are rare.
    """
    task = _norm(task_id)
    if not task:
        return default
    return read_task_modes().get(task, '') or default or configured_default_task_mode()
