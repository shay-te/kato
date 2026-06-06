"""Shared workspace repo-path helpers.

One source of truth for "which on-disk repo clones does a task have, and
which should Claude get as ``--add-dir``s beside its ``cwd``". Both the
chat-send route and the comment-run respawn need this; keeping it here
stops the two from drifting (and stops a comment-driven respawn from
spawning a single-repo session that can't reach the task's other repos —
the cross-repo "that repo is forbidden" refusal).
"""

from __future__ import annotations


def sibling_repository_dirs(workspace_manager, task_id: str, cwd: str) -> list[str]:
    """Repo clone paths for ``task_id`` EXCEPT ``cwd`` — for ``--add-dir``.

    Workspace mode: every repo folder under ``~/.kato/workspaces/<task>/``
    is surfaced so a multi-repo task's agent can read across repos (skip
    the ``cwd`` one — Claude already has it as its working directory).
    Empty list when there's no workspace (e.g. adopted-cwd tasks pointing
    at the dev's own checkout); we never probe parent dirs blindly.
    """
    if workspace_manager is None or not task_id:
        return []
    try:
        workspace = workspace_manager.get(task_id)
    except Exception:
        return []
    if workspace is None:
        return []
    repository_ids = list(getattr(workspace, 'repository_ids', None) or [])
    normalized_cwd = str(cwd or '').strip().rstrip('/\\')
    extras: list[str] = []
    seen: set[str] = set()
    for repo_id in repository_ids:
        try:
            repo_path = str(workspace_manager.repository_path(task_id, repo_id))
        except Exception:
            continue
        if not repo_path:
            continue
        normalized_repo = repo_path.rstrip('/\\')
        # Skip the cwd entry (Claude already has it) and dupes.
        if normalized_repo == normalized_cwd or normalized_repo in seen:
            continue
        seen.add(normalized_repo)
        extras.append(normalized_repo)
    return extras
