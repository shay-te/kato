"""When a split may be proposed, and how long its chat turn may take.

The split itself is proposed in the task's chat (``split_in_chat``).
"""

from __future__ import annotations

import os

from kato_core_lib.helpers.workspace_repo_utils import task_repository_clones

SPLIT_TIMEOUT_ENV = 'KATO_PLAN_SPLIT_TIMEOUT_SECONDS'
DEFAULT_SPLIT_TIMEOUT_SECONDS = 1800


def split_timeout_seconds() -> int:
    """How long a split may take — waiting for the chat, then its answer —
    in seconds, at least 60."""
    raw = os.environ.get(SPLIT_TIMEOUT_ENV, '')
    try:
        value = int(float(raw)) if raw.strip() else DEFAULT_SPLIT_TIMEOUT_SECONDS
    except (TypeError, ValueError):
        value = DEFAULT_SPLIT_TIMEOUT_SECONDS
    return max(60, value)


def split_refusal(task_id: str, *, workspace_manager) -> str:
    """Why a split can't be proposed for ``task_id`` right now, or ''."""
    if not task_repository_clones(workspace_manager, task_id):
        return 'the task has no repository clone to read the plan against'
    return ''
