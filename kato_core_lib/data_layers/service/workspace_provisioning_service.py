"""Glue between kato's task model and ``workspace_core_lib``.

Pure orchestration: clone every repo a kato task touches into the
task's workspace folder, write progress to the preflight log, and
hand back ``Repository`` shadows pointing at the clone paths so the
agent runs against isolated trees.

The generic workspace machinery (folder creation, metadata
persistence, preflight log I/O) lives in ``workspace_core_lib``.
This module owns the kato-specific pieces:

* Translating a kato ``Task`` + ``Repository`` list into workspace
  service calls.
* Coupling clone progress to ``log_mission_step`` so kato's mission
  feed mirrors the chat-side preflight log.
* The "clone failed → mark errored + raise" lifecycle policy that
  kato wants. Other consumers may want different behavior; they
  can call ``WorkspaceService`` directly.
"""

from __future__ import annotations

import copy
import logging

from workspace_core_lib.workspace_core_lib import (
    WORKSPACE_STATUS_ACTIVE,
    WORKSPACE_STATUS_ERRORED,
    WorkspaceService,
)

from kato_core_lib.helpers.mission_logging_utils import log_mission_step


_logger = logging.getLogger(__name__)


def provision_task_workspace_clones(
    workspace_service: 'WorkspaceService | None',
    repository_service,
    task,
    repositories: list,
):
    """Clone (or reuse) per-task workspace copies of ``repositories``.

    Returns shallow copies of the inventory ``Repository`` objects
    with ``local_path`` rewritten to point at the workspace clone
    path. The inventory originals are never mutated, so concurrent
    tasks never share branch state.

    No-op when ``workspace_service`` is None — the autonomous and
    wait-planning flows fall through to the legacy "use existing
    local clones" path. On any error after the workspace folder is
    created, the workspace is marked ``errored`` so the UI can
    prompt the user.
    """
    if workspace_service is None or not repositories:
        return repositories
    repository_ids = [
        getattr(r, 'id', '') for r in repositories if getattr(r, 'id', '')
    ]
    workspace_service.create(
        task_id=str(task.id),
        task_summary=str(getattr(task, 'summary', '') or ''),
        repository_ids=repository_ids,
    )
    total = len(repositories)
    workspace_service.append_preflight_log(
        str(task.id),
        f'preparing workspace ({total} repository(ies))',
    )
    provisioned: list = []
    try:
        for index, repository in enumerate(repositories, start=1):
            clone_path = workspace_service.repository_path(
                str(task.id), repository.id,
            )
            already_cloned = (clone_path / '.git').is_dir()
            # Two layers of progress: ``log_mission_step`` feeds the
            # orchestrator activity feed (right pane); the preflight
            # log feeds the chat tab (left pane). Operators glance
            # at whichever side they're looking at — both surfaces
            # stay in sync. ``cloning N/M:`` makes the progress bar
            # mental-model unmissable so they can see the queue.
            if already_cloned:
                workspace_service.append_preflight_log(
                    str(task.id),
                    f'cloning {index}/{total}: {repository.id} '
                    f'(already on disk, reusing)',
                )
            else:
                log_mission_step(
                    _logger,
                    str(task.id),
                    'cloning repository: %s (%d/%d)',
                    repository.id, index, total,
                )
                workspace_service.append_preflight_log(
                    str(task.id),
                    f'cloning {index}/{total}: {repository.id}',
                )
            repository_service.ensure_clone(repository, clone_path)
            workspace_service.append_preflight_log(
                str(task.id),
                f'✓ cloned {index}/{total}: {repository.id}',
            )
            rewritten = copy.copy(repository)
            rewritten.local_path = str(clone_path)
            provisioned.append(rewritten)
    except Exception as exc:
        workspace_service.append_preflight_log(
            str(task.id),
            f'✗ clone failed: {exc}',
        )
        workspace_service.update_status(str(task.id), WORKSPACE_STATUS_ERRORED)
        raise
    workspace_service.append_preflight_log(
        str(task.id),
        f'✓ all {total} repository(ies) cloned — starting agent',
    )
    workspace_service.update_status(str(task.id), WORKSPACE_STATUS_ACTIVE)
    return provisioned
