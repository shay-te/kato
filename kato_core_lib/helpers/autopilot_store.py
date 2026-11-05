"""The tasks on autopilot (``kato:autopilot``), remembered per task.

The tag lives on the ticket, but a chat is spawned — and its work finishes —
long after kato last read it. So whenever kato reads a task off the tracker it
records here whether the tag is on it (:func:`set_autopilot`), and the spawn
(Auto mode, the "finish by yourself" guidance) and the finish (Done pressed
for the operator) ask by task id (:func:`is_autopilot`). Taking the tag off
the ticket releases it on the next read.

Stored at ``~/.kato/autopilot_tasks.json`` (``KATO_AUTOPILOT_PATH``) as a list
of task ids; ids match case-insensitively, like the other per-task stores.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from utils_core_lib.utils_core_lib.atomic_write import atomic_write_json

from kato_core_lib.helpers.kato_paths_utils import kato_home_path

_ENV_KEY = 'KATO_AUTOPILOT_PATH'
_FILENAME = 'autopilot_tasks.json'
_lock = threading.Lock()


def _path() -> Path:
    return kato_home_path(_FILENAME, env_key=_ENV_KEY)


def _key(task_id: object) -> str:
    return str(task_id or '').strip().lower()


def _read() -> set[str]:
    try:
        data = json.loads(_path().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return set()
    return {_key(entry) for entry in data if isinstance(entry, str) and _key(entry)} if isinstance(data, list) else set()


def is_autopilot(task_id: object) -> bool:
    key = _key(task_id)
    return bool(key) and key in _read()


def set_autopilot(task_id: object, on: bool) -> bool:
    """Record whether ``task_id`` is on autopilot; True when that changed."""
    key = _key(task_id)
    if not key:
        return False
    with _lock:
        current = _read()
        if (key in current) == bool(on):
            return False
        current = current | {key} if on else current - {key}
        atomic_write_json(_path(), sorted(current), label='autopilot tasks')
        return True
