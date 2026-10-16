"""The model a task's ``kato:model:`` tag selects, remembered per task.

A tag lives on the ticket, but a chat is spawned long after kato last read it —
the operator's next message after the session went idle, a comment run, a
restart. None of those has the ticket in hand. So whenever kato reads a task off
the tracker it records here what the tag selects (:func:`set_task_model_tag`),
and every spawn asks for it by task id (:func:`task_model_tag`).

This is the task's STARTING model, not a lock: the operator's own pick in the
chat's model picker outranks it, exactly as it outranks the configured default.
Removing the tag from the ticket releases it on the next read.

Stored at ``~/.kato/task_model_tags.json`` (override via
``KATO_TASK_MODEL_TAGS_PATH``) as ``{task_id: model}`` so it survives a restart.
Ids match case-insensitively, like the planning-hold store: the tracker and the
UI do not always agree on case.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from utils_core_lib.utils_core_lib.atomic_write import atomic_write_json
from kato_core_lib.helpers.kato_paths_utils import kato_home_path

_ENV_KEY = 'KATO_TASK_MODEL_TAGS_PATH'
_FILENAME = 'task_model_tags.json'

# Read-modify-write against the whole file: two scan workers recording at the
# same moment would otherwise both read the old map and one entry is lost.
_lock = threading.Lock()


def _path() -> Path:
    return kato_home_path(_FILENAME, env_key=_ENV_KEY)


def _norm(value: object) -> str:
    return str(value or '').strip()


def _read() -> dict[str, str]:
    path = _path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {_norm(k): _norm(v) for k, v in data.items() if _norm(k) and _norm(v)}


def _key_for(models: dict[str, str], task: str) -> str:
    """The spelling ``task`` is already stored under, else ``task`` itself."""
    wanted = task.lower()
    return next((key for key in models if key.lower() == wanted), task)


def task_model_tag(task_id: object) -> str:
    """The model ``task_id``'s tag selects, or ``''`` when it has none."""
    task = _norm(task_id)
    if not task:
        return ''
    models = _read()
    return models.get(_key_for(models, task), '')


def set_task_model_tag(task_id: object, model: object) -> bool:
    """Record (or, with an empty ``model``, release) a task's model.

    True when that changed what is stored. Writes only on a change, so calling
    it for every task on every scan costs one small read.
    """
    task = _norm(task_id)
    wanted = _norm(model)
    if not task:
        return False
    with _lock:
        models = _read()
        key = _key_for(models, task)
        if models.get(key, '') == wanted:
            return False
        if wanted:
            models[key] = wanted
        else:
            models.pop(key, None)
        path = _path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        atomic_write_json(path, dict(sorted(models.items())))
    return True
