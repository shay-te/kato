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
from concurrent.futures import ThreadPoolExecutor, as_completed

from workspace_core_lib.workspace_core_lib import (
    WORKSPACE_STATUS_ACTIVE,
    WORKSPACE_STATUS_ERRORED,
    WorkspaceService,
)

from kato_core_lib.helpers.mission_logging_utils import log_mission_step


_logger = logging.getLogger(__name__)

_MAX_PARALLEL_CLONES = 4


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

    Clones run in parallel (up to _MAX_PARALLEL_CLONES at once) so a
    task with multiple repos doesn't pay the sum of clone times —
    it pays the max. On any error the workspace is marked errored and
    the exception re-raised.

    No-op when ``workspace_service`` is None — the autonomous and
    wait-planning flows fall through to the legacy "use existing
    local clones" path.
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
    task_id = str(task.id)
    workspace_service.append_preflight_log(
        task_id,
        f'preparing workspace ({total} repository(ies))',
    )

    # Announce all repos up front so the operator can see the queue,
    # then kick off all clones in parallel.
    clone_paths = []
    for index, repository in enumerate(repositories, start=1):
        clone_path = workspace_service.repository_path(task_id, repository.id)
        clone_paths.append(clone_path)
        already_cloned = (clone_path / '.git').is_dir()
        if already_cloned:
            workspace_service.append_preflight_log(
                task_id,
                f'cloning {index}/{total}: {repository.id} (already on disk, reusing)',
            )
        else:
            log_mission_step(
                _logger, task_id,
                'cloning repository: %s (%d/%d)', repository.id, index, total,
            )
            workspace_service.append_preflight_log(
                task_id, f'cloning {index}/{total}: {repository.id}',
            )

    provisioned: list = [None] * total
    try:
        workers = min(total, _MAX_PARALLEL_CLONES)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            index_futures = {
                executor.submit(repository_service.ensure_clone, repo, path): (i, repo, path)
                for i, (repo, path) in enumerate(zip(repositories, clone_paths))
            }
            for future in as_completed(index_futures):
                i, repository, clone_path = index_futures[future]
                future.result()  # re-raises on error
                workspace_service.append_preflight_log(
                    task_id, f'✓ cloned {i + 1}/{total}: {repository.id}',
                )
                rewritten = copy.copy(repository)
                rewritten.local_path = str(clone_path)
                provisioned[i] = rewritten
    except Exception as exc:
        workspace_service.append_preflight_log(task_id, f'✗ clone failed: {exc}')
        workspace_service.update_status(task_id, WORKSPACE_STATUS_ERRORED)
        raise

    workspace_service.append_preflight_log(
        task_id, f'✓ all {total} repository(ies) cloned — starting agent',
    )
    workspace_service.update_status(task_id, WORKSPACE_STATUS_ACTIVE)
    return provisioned
