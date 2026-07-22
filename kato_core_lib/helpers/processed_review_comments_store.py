"""Persistent record of the review comments kato has already addressed.

The review-comment scan re-picks any *unresolved* PR comment on every tick
unless kato remembers it already ran the agent against it. That memory —
``AgentStateRegistry.processed_review_comment_map`` — used to live in memory
only, so every kato RESTART wiped it and kato re-ran the agent and re-posted
its reply on every still-open comment. Comments are deliberately left
unresolved for the human reviewer (see ``review_comment_service``'s
no-auto-resolve policy), so a comment stayed eligible forever and got answered
over and over across restarts — the "same comment worked in a loop" symptom.

Persisting the marks here fixes that: the "already addressed" set survives a
restart, so a comment is worked exactly once until the reviewer resolves it (or
posts a follow-up, which the position-based gate re-engages by *new* comment
id). This complements ``forgotten_tasks_store`` — that stops a FORGOTTEN task
from being re-discovered; this stops a still-active task's answered comments
from being re-worked.

Keyed by ``(repository_id, pull_request_id)`` → the set of addressed
``comment_id``s, serialised as a flat list of records (JSON has no tuple keys).
Stored at ``~/.kato/processed_review_comments.json`` (override via
``KATO_PROCESSED_REVIEW_COMMENTS_PATH``). A task's entries are removed when the
operator deletes/forgets it (``AgentStateRegistry.forget_task``) so the file
never accumulates marks for pull requests that no longer belong to any task.
"""
from __future__ import annotations

import json
from pathlib import Path

from kato_core_lib.helpers.atomic_json_utils import atomic_write_json
from kato_core_lib.helpers.kato_paths_utils import kato_home_path

_ENV_KEY = 'KATO_PROCESSED_REVIEW_COMMENTS_PATH'
_FILENAME = 'processed_review_comments.json'

# Serialised record field names.
_REPO = 'repository_id'
_PR = 'pull_request_id'
_COMMENTS = 'comment_ids'


def default_path() -> Path:
    """``~/.kato/processed_review_comments.json`` (or the env override)."""
    return kato_home_path(_FILENAME, env_key=_ENV_KEY)


def read_processed_map(path: Path | str | None) -> dict[tuple[str, str], set[str]]:
    """Load the ``(repo, pr) -> {comment_id}`` map from ``path``.

    Best-effort: a missing, unreadable or malformed file yields an empty map
    (kato then simply treats every open comment as new — the pre-persistence
    behaviour — rather than crashing the boot).
    """
    result: dict[tuple[str, str], set[str]] = {}
    if not path or not Path(path).is_file():
        return result
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return result
    if not isinstance(data, list):
        return result
    for record in data:
        if not isinstance(record, dict):
            continue
        repository_id = str(record.get(_REPO, '') or '').strip()
        pull_request_id = str(record.get(_PR, '') or '').strip()
        if not repository_id or not pull_request_id:
            continue
        raw_ids = record.get(_COMMENTS, [])
        if not isinstance(raw_ids, list):
            continue
        comment_ids = {str(item).strip() for item in raw_ids if str(item).strip()}
        if comment_ids:
            result[(repository_id, pull_request_id)] = comment_ids
    return result


def write_processed_map(
    path: Path | str | None,
    processed_map: dict[tuple[str, str], set[str]],
) -> None:
    """Persist ``processed_map`` as a flat list of records (atomic, best-effort).

    ``processed_map`` MUST be a private snapshot the caller is done mutating —
    this iterates it, so a live map mutated by another thread would raise. The
    caller (``AgentStateRegistry``) copies under its lock before calling.
    """
    if not path:
        return
    records = [
        {
            _REPO: str(repository_id),
            _PR: str(pull_request_id),
            _COMMENTS: sorted(str(comment_id) for comment_id in comment_ids),
        }
        for (repository_id, pull_request_id), comment_ids in sorted(processed_map.items())
        if comment_ids
    ]
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    atomic_write_json(target, records)
