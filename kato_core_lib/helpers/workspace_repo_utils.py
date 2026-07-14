"""Shared workspace repo-path helpers.

One source of truth for "which on-disk repo clones does a task have, and
which should Claude get as ``--add-dir``s beside its ``cwd``". Both the
chat-send route and the comment-run respawn need this; keeping it here
stops the two from drifting (and stops a comment-driven respawn from
spawning a single-repo session that can't reach the task's other repos —
the cross-repo "that repo is forbidden" refusal).
"""

from __future__ import annotations


def sibling_repository_dirs(workspace_manager, task_id: str) -> list[str]:
    """The task's whole workspace folder, for ``--add-dir`` beyond ``cwd``.

    Workspace mode: returns ``<workspace_root>/<task_id>`` — the single
    parent folder every one of the task's repo clones lives under —
    rather than enumerating each currently-known ``repository_id``.
    The operator can attach another repo to the task mid-conversation;
    it clones into this SAME folder. A session's ``--add-dir`` set is
    baked in at spawn time and never widened later, so an enumerated
    repo list would miss a repo added after spawn until the session
    is respawned — scoping to the whole task folder covers it
    immediately (the "attach a new repo and kato still can't see it"
    bug). Empty list when there's no workspace (e.g. adopted-cwd tasks
    pointing at the dev's own checkout); we never probe parent dirs
    blindly.
    """
    if workspace_manager is None or not task_id:
        return []
    try:
        workspace = workspace_manager.get(task_id)
    except Exception:
        return []
    if workspace is None:
        return []
    try:
        task_root = str(workspace_manager.workspace_path(task_id))
    except Exception:
        return []
    if not task_root:
        return []
    return [task_root]
