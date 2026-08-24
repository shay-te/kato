"""Locating a task's local comment store.

The store lives inside the task's workspace, so "which store?" is a pure
function of the workspace manager and the task id — and every comment surface
needs to ask it (the CRUD path, the run engine, the remote sync). It answers
``None`` rather than raising for a task with no workspace on disk, because
every caller treats "no workspace" as "no comments", never as an error.
"""

from __future__ import annotations


def comment_store_for(workspace_manager, task_id: str):
    """Return the ``LocalCommentStore`` for ``task_id``, or ``None``.

    ``None`` means the task has no workspace directory (never provisioned,
    or already forgotten) — not that something failed.
    """
    from kato_core_lib.comment_core_lib import LocalCommentStore

    if workspace_manager is None:
        return None
    normalized = str(task_id or '').strip()
    if not normalized:
        return None
    try:
        workspace_dir = workspace_manager.workspace_path(normalized)
    except Exception:
        return None
    if not workspace_dir.is_dir():
        return None
    return LocalCommentStore(workspace_dir)
