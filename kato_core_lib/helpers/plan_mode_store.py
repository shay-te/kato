"""Persistent per-task plan-mode lock.

The composer's plan-mode toggle locks a task's chat session to
``--permission-mode plan`` (planning only, never edits). Unlike the
model/effort tuning knobs — which are deliberately ephemeral — plan mode
is a SAFETY lock, so it must survive a restart: kato persists the set of
plan-locked task ids here and reloads them into the live override map at
boot (see ``create_app``), so the next session respawn re-applies the lock.

Stored as ``[task_id, ...]`` at ``~/.kato/plan_mode.json`` (override via
``KATO_PLAN_MODE_PATH``). Task ids are stored verbatim — the canonical
platform id the UI sends — so they reload straight into the in-memory
override map the routes key by (which is NOT case-normalized).
"""
from __future__ import annotations

import json
from pathlib import Path

from kato_core_lib.helpers.atomic_json_utils import atomic_write_json
from kato_core_lib.helpers.kato_paths_utils import kato_home_path

_ENV_KEY = 'KATO_PLAN_MODE_PATH'
_FILENAME = 'plan_mode.json'


def _path() -> Path:
    return kato_home_path(_FILENAME, env_key=_ENV_KEY)


def _norm(task_id: object) -> str:
    return str(task_id or '').strip()


def read_plan_mode_tasks() -> set[str]:
    """The set of plan-locked task ids (empty when none / unreadable)."""
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


def _write(tasks: set[str]) -> None:
    path = _path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    atomic_write_json(path, sorted(tasks))


def set_plan_mode(task_id: str, on: bool) -> None:
    """Persist (``on``) or clear (``not on``) the plan-mode lock for a task.

    Best-effort + idempotent: a no-op task id is ignored, and writing the
    same state twice is harmless.
    """
    task = _norm(task_id)
    if not task:
        return
    tasks = read_plan_mode_tasks()
    if on:
        if task in tasks:
            return
        tasks.add(task)
    else:
        if task not in tasks:
            return
        tasks.discard(task)
    _write(tasks)
