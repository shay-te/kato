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
