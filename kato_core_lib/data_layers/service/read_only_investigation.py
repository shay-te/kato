"""One read-only agent run over a task's repositories.

The review loop's reviewer and the plan queue's split proposer both need the
same thing: a fresh, read-only run of the configured agent backend, started in
one of the task's repository clones (never the bare task folder), able to read
every other clone of the task, confined to the task folder. This is that run,
in one place, so the two can never set it up differently.
"""

from __future__ import annotations

import threading

from kato_core_lib.helpers.workspace_repo_utils import (
    sibling_repository_dirs,
    task_repository_clones,
    task_workspace_root,
)


def read_only_task_investigation(
    implementation_service,
    workspace_manager,
    task_id: str,
    prompt: str,
    *,
    log_label: str,
    cancel_event: threading.Event,
    timeout_seconds: int = 0,
    model: str = '',
) -> str:
    """The agent's whole reply to ``prompt`` (read-only: it changes nothing)."""
    clones = task_repository_clones(workspace_manager, task_id)
    return implementation_service.investigate(
        prompt,
        # In a repo clone, never the bare task folder (see
        # ``resolve_session_cwd``); the task folder is reachable through
        # ``additional_dirs`` and, in docker mode, the mount.
        cwd=clones[0] if clones else '',
        additional_dirs=sibling_repository_dirs(workspace_manager, task_id),
        sandbox_root=task_workspace_root(workspace_manager, task_id),
        task_id=task_id,
        log_label=log_label,
        cancel_event=cancel_event,
        timeout_seconds=timeout_seconds,
        # '' = the backend's configured model.
        model=model,
    )
