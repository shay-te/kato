"""How far kato takes a task by itself — its automation level — remembered per task.

Two ticket tags set it (``kato_tag_utils.automation_level_from_tags``):

* ``kato:autopilot`` → :data:`AUTOPILOT`: Auto mode, the work, its review loop if
  tagged, and Done pressed for the operator (push, pull request, ticket);
* ``kato:auto-wait`` → :data:`AUTO_WAIT`: Auto mode, the work, the review loop —
  and then it waits: the operator presses Done. With both tags, auto-wait wins:
  "do not press Done" is the stricter statement.

The tags live on the ticket, but a chat is spawned — and its work finishes —
long after kato last read it. So whenever kato reads a task off the tracker it
records the level here (:func:`set_automation_level`), and the spawn (Auto
mode, the guidance), the park (its review loop) and the finish (Done) ask by
task id. Taking the tag off the ticket releases it on the next read. This is
the ONE answer to "how far does kato take this task" — ask it, never the tags.

Stored at ``~/.kato/autopilot_tasks.json`` (``KATO_AUTOPILOT_PATH``) as
``{task id: level}``; ids match case-insensitively, like the other per-task
stores. A file from before auto-wait existed is a plain list of ids, all on
autopilot, and still reads that way.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from utils_core_lib.utils_core_lib.atomic_write import atomic_write_json

from kato_core_lib.helpers.kato_paths_utils import kato_home_path

AUTOPILOT = 'autopilot'
AUTO_WAIT = 'auto-wait'
LEVELS = (AUTOPILOT, AUTO_WAIT)

_ENV_KEY = 'KATO_AUTOPILOT_PATH'
_FILENAME = 'autopilot_tasks.json'
_lock = threading.Lock()


def _path() -> Path:
    return kato_home_path(_FILENAME, env_key=_ENV_KEY)


def _key(task_id: object) -> str:
    return str(task_id or '').strip().lower()


def _read() -> dict[str, str]:
    try:
        data = json.loads(_path().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    if isinstance(data, list):  # before auto-wait: every id listed was on autopilot
        return {_key(entry): AUTOPILOT for entry in data if isinstance(entry, str) and _key(entry)}
    if not isinstance(data, dict):
        return {}
    return {
        _key(task): level for task, level in data.items()
        if isinstance(task, str) and _key(task) and level in LEVELS
    }


def automation_level(task_id: object) -> str:
    """:data:`AUTOPILOT`, :data:`AUTO_WAIT`, or '' — kato takes it no further than asked."""
    key = _key(task_id)
    return _read().get(key, '') if key else ''


def automation_levels() -> dict[str, str]:
    """Every task's level, lower-cased id → level: one read for a whole list
    (the tasks rail asks for all of them every few seconds)."""
    return _read()


def is_autopilot(task_id: object) -> bool:
    """Does kato press Done for this task? Only on autopilot."""
    return automation_level(task_id) == AUTOPILOT


def is_auto_wait(task_id: object) -> bool:
    """Does kato do the work and its review loop, then wait for the operator's Done?"""
    return automation_level(task_id) == AUTO_WAIT


def runs_by_itself(task_id: object) -> bool:
    """Auto mode, and the agent finishes the work without waiting to be told."""
    return automation_level(task_id) in LEVELS


def set_automation_level(task_id: object, level: str) -> bool:
    """Record ``task_id``'s level ('' releases it); True when that changed."""
    key = _key(task_id)
    if not key:
        return False
    wanted = level if level in LEVELS else ''
    with _lock:
        current = _read()
        if current.get(key, '') == wanted:
            return False
        if wanted:
            current[key] = wanted
        else:
            current.pop(key, None)
        # Raises on a failed write: answering "changed" for a write that did
        # not land would log a level the next read cannot find.
        atomic_write_json(
            _path(), dict(sorted(current.items())), label='automation levels', raise_on_error=True,
        )
        return True
