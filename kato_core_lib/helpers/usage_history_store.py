"""What a deleted task used — the one record of it left once its transcripts are gone.

The Usage dialog counts tokens from the agent's transcripts. Deleting a task
(DELETE ``/api/sessions/<task_id>/workspace``) deletes those transcripts on
purpose — a forgotten conversation must not stay resumable — so without this
file a deleted task's tokens would vanish from the dialog and the grand total
would shrink every time the operator tidied up.

Before the delete, the route snapshots the task's usage totals and keeps them
here. NUMBERS ONLY: token counts, per-model counts, the estimated cost, the
dates. No prompt, reply, file path or repository — the conversation is still
erased, and this file is nothing to resume.

Stored at ``~/.kato/usage_history.json`` (override via
``KATO_USAGE_HISTORY_PATH``). A task deleted before this existed left nothing
behind and cannot be recovered.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from utils_core_lib.utils_core_lib.atomic_write import atomic_write_json
from kato_core_lib.helpers.kato_paths_utils import kato_home_path

_ENV_KEY = 'KATO_USAGE_HISTORY_PATH'
_FILENAME = 'usage_history.json'

# Appending is read-modify-write on the whole file; two deletes close together
# would otherwise both read the old list and the second write would drop the
# first's entry.
_lock = threading.Lock()


def _path() -> Path:
    return kato_home_path(_FILENAME, env_key=_ENV_KEY)


def deleted_task_usage() -> list[dict]:
    """The recorded entries, oldest first — empty on a missing or corrupt file."""
    path = _path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [
        entry for entry in data
        if isinstance(entry, dict) and str(entry.get('task_id') or '').strip()
        and isinstance(entry.get('usage'), dict)
    ]


def record_deleted_task_usage(entry: dict) -> None:
    """Keep ``entry`` (a task's usage snapshot) after the task is deleted."""
    with _lock:
        entries = deleted_task_usage()
        entries.append(entry)
        path = _path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        atomic_write_json(path, entries)
