"""Tasks held in Plan by the ``kato:wait-planning`` tag.

The tag means "discuss, don't edit", and kato honoured it on exactly one
spawn: the hold session it opens when it first sees the tag. Every later
spawn — the operator's next message after that session went idle, a comment
run, a restart — went through the ordinary spawn path with no mode, fell back
to the configured default (acceptEdits), and the composer showed "Edit
automatically". The agent then edited files "even while I am discussing things
with him".

So the tag is recorded here as a HOLD, kept apart from the operator's own mode
pick in ``plan_mode_store``: the hold is kato's reading of the ticket, the pick
is the operator's choice, and releasing one must not erase the other. While a
task is held every spawn runs ``--permission-mode plan`` whatever the pick says
(:func:`held_permission_mode`); the hold is released only when kato reads the
ticket without the tag.

Stored at ``~/.kato/planning_holds.json`` (override via
``KATO_PLANNING_HOLD_PATH``) as a sorted list of task ids, so a held task stays
held across a restart. Ids match case-insensitively, like the forgotten-task
store: the tracker and the UI do not always agree on case.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from utils_core_lib.utils_core_lib.atomic_write import atomic_write_json
from kato_core_lib.helpers.kato_paths_utils import kato_home_path
from kato_core_lib.helpers.plan_mode_store import PLAN_MODE

_ENV_KEY = 'KATO_PLANNING_HOLD_PATH'
_FILENAME = 'planning_holds.json'

# Read-modify-write against the whole file: without this, two scan workers
# recording holds at the same moment can both read the old list and one of the
# holds is lost. Same pattern as plan_mode_store.
_lock = threading.Lock()


def _path() -> Path:
    return kato_home_path(_FILENAME, env_key=_ENV_KEY)


def _norm(task_id: object) -> str:
    return str(task_id or '').strip()


def read_planning_holds() -> set[str]:
    """Every held task id (empty when none / unreadable)."""
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


def task_is_planning_held(task_id: object) -> bool:
    """Whether ``task_id`` is held in Plan by its planning tag."""
    wanted = _norm(task_id).lower()
    if not wanted:
        return False
    return any(held.lower() == wanted for held in read_planning_holds())


def set_planning_hold(task_id: object, held: bool) -> bool:
    """Record (``held``) or release a task's hold; True when that changed it.

    Writes only on a change, so calling it for every task on every scan costs
    one small read.
    """
    task = _norm(task_id)
    if not task:
        return False
    with _lock:
        holds = read_planning_holds()
        others = {item for item in holds if item.lower() != task.lower()}
        updated = others | {task} if held else others
        if {item.lower() for item in updated} == {item.lower() for item in holds}:
            return False
        path = _path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        atomic_write_json(path, sorted(updated))
    return True


def held_permission_mode(task_id: object, requested: object = '') -> str:
    """``plan`` while ``task_id`` is held, otherwise ``requested`` unchanged.

    The one rule every spawn and the composer's mode read go through.
    """
    if task_is_planning_held(task_id):
        return PLAN_MODE
    return _norm(requested)
