"""Persistent set of task ids the operator explicitly forgot.

Forgetting a task (DELETE ``/api/sessions/<task_id>/workspace``) wipes its local
workspace clones + session record — but the task can still be IN REVIEW on the
platform (YouTrack/Jira/Bitbucket) with unresolved PR comments. The review-comment
scan polls the PLATFORM for in-review tasks (``TaskService.get_review_tasks``),
so without a persistent marker a forgotten task gets re-discovered and
resurrected on the next scan. This is especially visible after a restart, which
clears the in-memory ``AgentStateRegistry.processed_review_comment_map`` so every
comment looks new again — a task forgotten days ago "pops up from nothing".

This file records the forgotten ids on disk so the scan skips them until the
operator RE-ADOPTS the task (adopt clears the mark). It does NOT touch the
platform — kato never mutates the ticket; it just stops re-engaging locally.

Stored at ``~/.kato/forgotten_tasks.json`` (override via
``KATO_FORGOTTEN_TASKS_PATH``).
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from utils_core_lib.utils_core_lib.atomic_write import atomic_write_json
from kato_core_lib.helpers.kato_paths_utils import kato_home_path

_ENV_KEY = 'KATO_FORGOTTEN_TASKS_PATH'
_FILENAME = 'forgotten_tasks.json'

# forget()/unforget() are read-modify-write against the whole file — without
# this lock, two calls close together (e.g. forget() racing unforget() on
# re-adopt) can both read the old set before either writes, silently
# reverting one call's change. Mirrors tool_decision_store.py's pattern.
_lock = threading.Lock()


def _path() -> Path:
    return kato_home_path(_FILENAME, env_key=_ENV_KEY)


def forgotten_task_ids() -> set[str]:
    """Return the forgotten task ids — empty set on a missing/corrupt file."""
    path = _path()
    if not path.is_file():
        return set()
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return set()
    if not isinstance(data, list):
        return set()
    return {str(item).strip() for item in data if str(item).strip()}


def _normalize(task_id: object) -> str:
    """Canonical key for forgotten-id membership tests.

    Task ids reach the scan with disagreeing casing — the ticket platform
    yields ``UNA-1495`` while on-disk records/workspaces are lowercased
    (``una-1495``). A case-sensitive test silently fails to skip a forgotten
    task and resurrects it on the next scan, so every membership check
    compares on this ``.strip().lower()`` key (the same policy as
    ``AgentService._norm_task_id``). The ORIGINAL case is what's stored.
    """
    return str(task_id or '').strip().lower()


def is_forgotten(task_id: str) -> bool:
    normalized = _normalize(task_id)
    return bool(normalized) and normalized in {
        _normalize(item) for item in forgotten_task_ids()
    }


def _is_plausible_task_id(value: str) -> bool:
    """Whether ``value`` could name a task on any supported platform.

    Deliberately permissive — ticket ids differ wildly across trackers
    (``UNA-2913``, a GitHub issue's ``1247``, a GitLab ``group/proj#4``).
    This only rejects what can NEVER be one: traversal tokens and path
    separators, which arrive from a URL segment rather than a tracker.
    """
    if value in ('.', '..'):
        return False
    return not any(separator in value for separator in ('/', '\\', os.sep))


def forget(task_id: str) -> None:
    """Mark a task forgotten so the scan skips it until it is re-adopted.

    Ids that cannot name a task are rejected rather than stored. The
    DELETE route takes the id straight from the URL and marks it
    forgotten BEFORE the workspace layer gets a chance to reject it, so
    anything the caller sends lands here — which is how ``..``,
    ``lessons`` and ``lesson-candidates`` ended up in a real operator's
    file. That matters beyond untidiness: every id in this file is
    silently skipped by the review-comment scan, so junk here is a
    standing instruction to ignore work.
    """
    raw = str(task_id or '').strip()
    if not raw or not _is_plausible_task_id(raw):
        return
    with _lock:
        ids = forgotten_task_ids()
        # Dedup case-insensitively — ``UNA-1495`` and ``una-1495`` are the
        # same task, so the file never accumulates case-variant duplicates.
        if _normalize(raw) in {_normalize(item) for item in ids}:
            return
        ids.add(raw)
        _write(ids)


def unforget(task_id: str) -> None:
    """Clear a task's forgotten mark — the operator re-adopted it."""
    normalized = _normalize(task_id)
    if not normalized:
        return
    with _lock:
        ids = forgotten_task_ids()
        # Drop EVERY case-variant of the id — the mark may have been
        # written in a different case than the one the operator re-adopts in.
        remaining = {item for item in ids if _normalize(item) != normalized}
        if remaining == ids:
            return
        _write(remaining)


def _write(ids: set[str]) -> None:
    path = _path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    atomic_write_json(path, sorted(ids))
