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

from kato_core_lib.helpers.preflight_log_utils import (
    clone_failed_message,
    cloned_message,
    cloning_message,
    preparing_message,
    ready_message,
)
from kato_core_lib.helpers.mission_logging_utils import log_mission_step


_logger = logging.getLogger(__name__)

_MAX_PARALLEL_CLONES = 4


class WorkspaceCloneError(RuntimeError):
    """Some repositories failed to clone; ``failures`` says which, and why.

    A plain RuntimeError carried only the joined message, so every caller that
    wanted to report per repository had to blame ALL of them — the Files-tab
    sync did exactly that, and named a repository that had cloned fine
    (UNA-2417: "ob-love-ui: failed to clone …" describing
    objective_love_core_lib's failure).
    """

    def __init__(self, message: str, failures: dict[str, str]) -> None:
        super().__init__(message)
        self.failures = dict(failures)


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
    # UNION with what the workspace already holds — never a replacement.
    #
    # ``WorkspaceService.create`` overwrites ``repository_ids`` whenever the
    # list it is handed is non-empty, which is right for a fresh workspace
    # and wrong for every later call. Any provisioning whose resolution
    # returns a SUBSET of what is already on disk therefore erased the rest:
    # adding one repo through the Files tab took a workspace from
    # ``['alpha', 'beta']`` to ``['gamma']``. The clones stayed on disk, on
    # their task branch, holding the agent's work — but kato had forgotten
    # them, so push and pull-request silently skipped them and re-syncing
    # never repaired it.
    #
    # Kato's own rule is that a workspace NEVER loses a repo ("Never removes
    # repos from the workspace" — sync_task_repositories), so the union is
    # what that promise actually requires. Order is preserved, existing
    # entries first, so the metadata stays stable across ticks.
    existing_ids: list[str] = []
    try:
        existing = workspace_service.get(str(task.id))
        existing_ids = [str(rid) for rid in (existing.repository_ids or [])] if existing else []
    except Exception:
        existing_ids = []
    seen = set()
    repository_ids = [
        rid for rid in [*existing_ids, *repository_ids]
        if rid and not (rid in seen or seen.add(rid))
    ]
    workspace_service.create(
        task_id=str(task.id),
        task_summary=str(getattr(task, 'summary', '') or ''),
        # Cached so a later chat spawn can open with the ticket's own text
        # without re-querying the tracker (which the scan cadence already
        # rations to stay under provider rate limits).
        task_description=str(getattr(task, 'description', '') or ''),
        repository_ids=repository_ids,
    )
    total = len(repositories)
    task_id = str(task.id)
    clone_paths = [
        workspace_service.repository_path(task_id, repository.id)
        for repository in repositories
    ]
    # Only the repositories NOT on disk yet are announced, numbered among
    # themselves. Reusing a clone is not an event: a line per repository on
    # every preparation put fifty lines of "already on disk, reusing" into the
    # chat of every task reopened after a restart (see preflight_log_utils).
    # Nothing to clone means nothing is written.
    to_clone = [
        index for index, clone_path in enumerate(clone_paths)
        if not (clone_path / '.git').is_dir()
    ]
    positions = {index: position for position, index in enumerate(to_clone, start=1)}
    if to_clone:
        workspace_service.append_preflight_log(
            task_id, preparing_message(len(to_clone), total),
        )
    for index in to_clone:
        repository = repositories[index]
        log_mission_step(
            _logger, task_id,
            'cloning repository: %s (%d/%d)', repository.id, index + 1, total,
        )
        workspace_service.append_preflight_log(
            task_id, cloning_message(positions[index], len(to_clone), repository.id),
        )

    provisioned: list = [None] * total
    # PER-REPO ISOLATION, the same rule ``prepare_task_branches`` already
    # learned one layer down: "One repo's fault must not cost the others their
    # task branch."
    #
    # This collected the FIRST exception and re-raised it immediately, so a
    # single unreachable repo abandoned every clone still running and every
    # repo behind it — and because the raise happened before branch prep, the
    # ones that HAD cloned were left sitting on the remote's default branch.
    # That is the reported "he cloned all the repos but all the repos are
    # still on master and not on the task branch": one failure, and the whole
    # task's provisioning was thrown away.
    #
    # Every repo is attempted now, and the failures are raised TOGETHER at the
    # end — so the task still fails loudly (it must: an agent must never work
    # against a workspace that is quietly missing a repository), but the
    # operator gets the full list instead of whichever one happened to fail
    # first, and the clones that succeeded are on disk for the retry.
    failures: list[str] = []
    failures_by_repository: dict[str, str] = {}
    try:
        workers = min(total, _MAX_PARALLEL_CLONES)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            index_futures = {
                executor.submit(repository_service.ensure_clone, repo, path): (i, repo, path)
                for i, (repo, path) in enumerate(zip(repositories, clone_paths))
            }
            for future in as_completed(index_futures):
                i, repository, clone_path = index_futures[future]
                try:
                    future.result()
                except Exception as exc:
                    _logger.exception(
                        'failed to clone %s for task %s; continuing with the '
                        'other repositories', repository.id, task_id,
                    )
                    failures.append(f'{repository.id}: {exc}')
                    failures_by_repository[str(repository.id)] = str(exc)
                    # Always shown — a failure matters even for a reused clone.
                    workspace_service.append_preflight_log(
                        task_id, clone_failed_message(repository.id, exc),
                    )
                    continue
                if i in positions:
                    workspace_service.append_preflight_log(
                        task_id, cloned_message(positions[i], len(to_clone), repository.id),
                    )
                rewritten = copy.copy(repository)
                rewritten.local_path = str(clone_path)
                provisioned[i] = rewritten
        if failures:
            raise WorkspaceCloneError(
                f'failed to clone {len(failures)} of {total} repositor'
                f'{"y" if len(failures) == 1 else "ies"} — the rest were '
                f'cloned: {"; ".join(failures)}',
                failures_by_repository,
            )
    except Exception as exc:
        workspace_service.append_preflight_log(task_id, f'✗ clone failed: {exc}')
        workspace_service.update_status(task_id, WORKSPACE_STATUS_ERRORED)
        # Onto the STATUS FEED, not just the preflight log. The preflight log
        # is replayed into a session's own stream, so it is only ever seen by
        # someone already looking at that task's chat — and a failure here
        # means there may be no usable chat to look at. The mission-log shape
        # is what ``classifyStatusEntry`` reads to raise a notification, so
        # this is the line that actually reaches the operator wherever they
        # are. Without it a clone failure degraded in silence: the Files pane
        # was simply empty, with nothing saying why.
        log_mission_step(
            _logger, task_id, 'repository clone failed: %s', exc,
        )
        raise

    if to_clone:
        workspace_service.append_preflight_log(task_id, ready_message(len(to_clone)))
    workspace_service.update_status(task_id, WORKSPACE_STATUS_ACTIVE)
    return provisioned
