"""Publishing a task's work: push, pull, merge, pull requests, source sync.

The second subsystem extracted from ``AgentService`` (after
``TaskCommentService``). Thirteen public methods that share one job — moving a
task's commits between the workspace clone, the operator's source tree, and
the provider — and one private helper, ``_resolve_publish_context``, that every
one of them starts with.

It also owns the push-approval hold: kato never publishes on its own, so a
finished task parks in ``_pending_publish`` until the operator presses the
button and ``approve_push`` resumes it. That state lives here because
everything that reads it lives here.

Collaborators can be handed over as the object itself or, when the host
replaces them at runtime, wrapped in ``later(host, 'attr')`` so they resolve
per call — a reference frozen at build time is how a service ends up talking
to a workspace manager nobody else uses.
"""

from __future__ import annotations

from kato_core_lib.helpers.deadline import run_with_deadline
from kato_core_lib.helpers.late_binding import provider_for
from kato_core_lib.helpers.service_results import failure
from kato_core_lib.helpers.logging_utils import configure_logger
from utils_core_lib.utils_core_lib.text_utils import text_from_mapping
import copy
from types import SimpleNamespace
from dataclasses import dataclass
from kato_core_lib.data_layers.service.repository_service import (
    RepositoryHasNoChangesError,
)

_ON_DEMAND_PUSH_EXPECTED_ERRORS = (RepositoryHasNoChangesError,)

# How long the pre-push tag reconcile may take before publishing proceeds
# without it. It reads the ticket platform; a provider backoff must not hold
# an operator's git action.
_RECONCILE_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class _SourceRepoOutcome(object):
    """What updating ONE operator clone did: updated, skipped, or failed.

    Returned per repository so the caller can bucket the run without a
    hundred-line loop appending into four lists as it goes.
    """

    kind: str
    repository_id: str
    reason: str = ''
    error: str = ''
    warning: dict | None = None


@dataclass(frozen=True)
class _PublishTaskLite(object):
    """Minimal Task-shaped object for on-demand push / PR-creation.

    The repository service's ``build_branch_name`` and the publication
    path only read ``.id`` and ``.summary``; carrying a full ``Task``
    (with tags, comments, watchers, etc.) here would require re-fetching
    from the ticket system on every button click.
    """

    id: str
    summary: str = ''


class TaskPublishService(object):
    """Push / pull / merge / PR for one task's repositories."""

    def __init__(
        self,
        *,
        repository_service,
        task_service,
        task_state_service,
        task_publisher,
        workspace_manager,
        lesson_service=None,
        pending_publish=None,
        pending_publish_lock=None,
        update_workspace_status_after_publish=None,
        reconcile_task_repositories=None,
        logger=None,
    ) -> None:
        # Collaborators may arrive as the object itself or wrapped in
        # ``later(host, 'attr')`` — see kato_core_lib.helpers.late_binding.
        self._get_repository_service = provider_for(repository_service)
        self._get_task_service = provider_for(task_service)
        self._get_task_state_service = provider_for(task_state_service)
        self._get_task_publisher = provider_for(task_publisher)
        self._get_workspace_manager = provider_for(workspace_manager)
        self._get_lesson_service = provider_for(lesson_service)
        # The push-approval hold is SHARED with the host: the autonomous flow
        # parks a finished task there and the UI's approve button resumes it,
        # so both objects must see the same dict and the same lock.
        self._pending_publish = pending_publish if pending_publish is not None else {}
        self._pending_publish_lock = pending_publish_lock
        self._update_workspace_status_after_publish = update_workspace_status_after_publish
        # Folding the ticket's ``kato:repo:`` tags into the workspace metadata
        # belongs to the repositories subsystem; publishing only needs it run
        # before it reads that metadata. Best-effort by default.
        self._reconcile_task_repositories_impl = (
            reconcile_task_repositories or (lambda task_id: None)
        )
        self._logger_getter = provider_for(
            logger if logger is not None else configure_logger('TaskPublishService'),
        )

    @property
    def logger(self):
        """The host's CURRENT logger — resolved per call, never captured."""
        return self._logger_getter()

    @property
    def _repository_service(self):
        return self._get_repository_service()

    @property
    def _task_service(self):
        return self._get_task_service()

    @property
    def _task_state_service(self):
        return self._get_task_state_service()

    @property
    def _task_publisher(self):
        return self._get_task_publisher()

    @property
    def _workspace_manager(self):
        return self._get_workspace_manager()

    @property
    def _lesson_service(self):
        """Lesson capture. ``None`` when the host runs without lessons."""
        return self._get_lesson_service()

    def _reconcile_task_repositories(self, task_id: str):
        """Fold the ticket's repo tags into the metadata — bounded, best-effort.

        Runs BEFORE push and PR so a repo tagged onto the task after the
        workspace was built is not silently skipped. It reads the ticket
        platform, which means it can be slow for reasons that have nothing to
        do with git: a provider rate-limit backoff is tens of seconds, and an
        operator pressing Push should never watch the button hang on one.

        On timeout the git work proceeds against the repositories already in
        the workspace metadata — the same fallback this already had for a
        reconcile that ERRORS. A repo tagged in the last few seconds is then
        picked up by the next scan tick or the next press, which is a far
        better outcome than a frozen button.

        ALWAYS a dict, never None. ``push_task`` reads ``added_repositories``
        off this to tell the operator which repos the push covered, and both
        no-result paths used to hand it ``None`` instead — the deadline's
        default, and the no-op stand-in used when no reconcile function is
        injected. Either one turned a slow ticket provider into an
        ``AttributeError`` 500 on Push and Update-source, i.e. the timeout
        fallback took down the very operation it existed to protect.
        """
        result = run_with_deadline(
            lambda: self._reconcile_task_repositories_impl(task_id),
            seconds=_RECONCILE_TIMEOUT_SECONDS,
            default=None,
            on_timeout=lambda: self.logger.warning(
                'repository reconcile for task %s exceeded %ss; publishing '
                'with the repositories already in the workspace metadata',
                task_id, _RECONCILE_TIMEOUT_SECONDS,
            ),
        )
        return result if isinstance(result, dict) else {}

    def recheck_repository_push_access(self, task_id: str, repo_id: str) -> bool:
        """Re-run the push-access check for one repo's workspace clone.

        Powers the planning UI's "try again" on a read-only repo badge: if push
        access has since been granted the repo is dropped from the read-only
        store (it becomes writable again); otherwise it stays read-only.
        Returns ``True`` when the repo is now pushable.
        """
        import copy
        from kato_core_lib.helpers.read_only_repos_store import (
            clear_read_only_repo,
            read_only_repos,
            set_read_only_repos,
        )
        workspace_manager = getattr(self, '_workspace_manager', None)
        if workspace_manager is None:
            return False
        clone_path = workspace_manager.repository_path(task_id, repo_id)
        try:
            repository = copy.copy(self._repository_service.get_repository(repo_id))
        except Exception:
            self.logger.exception(
                'recheck: cannot resolve repository %s for task %s', repo_id, task_id,
            )
            return False
        repository.local_path = str(clone_path)
        # The task branch is the task id (build_branch_name == normalized_text
        # of the id, i.e. just stripped).
        branch_name = str(task_id).strip()
        pushable = self._repository_service.is_branch_pushable(repository, branch_name)
        if pushable:
            clear_read_only_repo(task_id, repo_id)
        else:
            still = read_only_repos(task_id)
            still.add(str(repo_id))
            set_read_only_repos(task_id, still)
        return pushable

    def approve_push(self, task_id: str) -> dict[str, object] | None:
        """Operator-triggered push for a task paused on ``kato:wait-before-git-push``.

        Returns the publish result on success, or ``None`` when no pending
        publish exists for the task (e.g. operator clicked the button on a
        task that wasn't paused, or kato restarted and lost the in-memory
        pending state).
        """
        normalized_task_id = str(task_id or '').strip()
        if not normalized_task_id:
            return None
        with self._pending_publish_lock:
            pending = self._pending_publish.pop(normalized_task_id, None)
        if pending is None:
            return None
        task, prepared_task, execution = pending
        self.logger.info(
            'operator approved push for task %s; resuming publish',
            normalized_task_id,
        )
        return self.publish_execution(task, prepared_task, execution)

    def publish_execution(self, task, prepared_task, execution):
        """Publish a finished task's work and record the outcome on its workspace.

        The single way work reaches the provider, whether the autonomous flow
        got there or the operator pressed the button. The two steps belong
        together: a publish whose workspace status is never written leaves the
        UI showing a task that is still working.
        """
        publish_result = self._task_publisher.publish_task_execution(
            task,
            prepared_task,
            execution,
        )
        self._update_workspace_status_after_publish(task.id, publish_result)
        return publish_result

    def is_awaiting_push_approval(self, task_id: str) -> bool:
        """True when ``approve_push`` has a pending publish for this task."""
        normalized = str(task_id or '').strip()
        if not normalized:
            return False
        with self._pending_publish_lock:
            return normalized in self._pending_publish


    def update_source_for_task(self, task_id: str) -> dict[str, object]:
        """Push + sync the operator's REPOSITORY_ROOT_PATH clones to the task branch.

        Drives the planning UI's "Update source" button. Pure git
        plumbing — no AI involvement. Two phases:

        1. ``push_task(task_id)`` — pushes the per-task workspace
           clone's branch to origin so the remote has the latest
           commits to pull from.
        2. For each repository the task touches, switch the operator's
           clone under ``REPOSITORY_ROOT_PATH`` to the task branch
           (see :meth:`_update_one_source_repository`).

        Returns a per-repo summary the UI renders in the toast.
        """
        normalized = str(task_id or '').strip()
        if not normalized:
            return failure('empty task id', flag='updated', task_id=task_id)
        push_result = self.push_task(normalized)
        # Even if push partially failed, attempt to update the
        # source for the repos that DID push — partial success is
        # still useful (tester can see whatever made it to origin).
        repos, _branch, task_obj = self._resolve_publish_context(normalized)
        if not repos:
            return failure(
                'no workspace context for this task',
                flag='updated',
                task_id=normalized,
                pushed=push_result,
            )
        updated_repositories: list[str] = []
        skipped_repositories: list[dict[str, str]] = []
        failed_repositories: list[dict[str, str]] = []
        # Per-repo warnings produced by ``update_source_to_task_branch``
        # — e.g. "stashed your changes and reapplied with conflicts".
        # Surfaced to the UI toast so the operator knows the repo did
        # update but they have something to clean up.
        warnings_per_repo: list[dict[str, object]] = []
        for repository in repos:
            outcome = self._update_one_source_repository(
                normalized,
                repository,
                self._repository_service.build_branch_name(task_obj, repository),
            )
            if outcome.kind == 'updated':
                updated_repositories.append(outcome.repository_id)
                if outcome.warning is not None:
                    warnings_per_repo.append(outcome.warning)
            elif outcome.kind == 'skipped':
                skipped_repositories.append({
                    'repository_id': outcome.repository_id,
                    'reason': outcome.reason,
                })
            else:
                failed_repositories.append({
                    'repository_id': outcome.repository_id,
                    'error': outcome.error,
                })
        # A single completion line (root-logged → the planning UI's status
        # feed) so the UI can fire an OS "Source updated" notification when the
        # operator is on another window. Big / multi-repo updates take a while;
        # this is the "it's done" ping. Uses the ``Mission <task>:`` convention
        # the frontend's classifyStatusEntry matches.
        self.logger.info(
            'Mission %s: source update finished (%d updated, %d skipped, %d failed)',
            normalized, len(updated_repositories), len(skipped_repositories),
            len(failed_repositories),
        )
        return {
            'updated': bool(updated_repositories),
            'task_id': normalized,
            'pushed': push_result,
            'updated_repositories': updated_repositories,
            'skipped_repositories': skipped_repositories,
            'failed_repositories': failed_repositories,
            'warnings': warnings_per_repo,
        }

    def _update_one_source_repository(
        self, task_id: str, repository, branch_name: str,
    ) -> '_SourceRepoOutcome':
        """Switch ONE operator clone to the task branch; report what happened.

        ``repository`` is the workspace-clone view (``_resolve_publish_context``
        rewrote ``local_path`` to the workspace path); the clone this actually
        touches is the inventory's ``local_path`` under REPOSITORY_ROOT_PATH —
        the operator's running system, never a kato scratch space.

        Skips rather than fails whenever there is nothing to propagate: no
        task-branch commits and a clean workspace tree would mean yanking the
        operator off whatever branch they were on for no reason.
        """
        try:
            has_changes = self._repository_service.workspace_has_task_changes(
                repository, branch_name,
            )
        except Exception:
            self.logger.exception(
                'workspace-has-changes pre-check failed for task %s '
                'repository %s',
                task_id, repository.id,
            )
            has_changes = True
        self.logger.info(
            'update-source for task %s: %s has_changes=%s '
            '(workspace=%s, branch=%s)',
            task_id, repository.id, has_changes,
            getattr(repository, 'local_path', '<unknown>'), branch_name,
        )
        if not has_changes:
            return _SourceRepoOutcome(
                'skipped', repository.id, reason='no changes in workspace clone',
            )
        try:
            source_repo = self._repository_service.get_repository(repository.id)
        except ValueError as exc:
            return _SourceRepoOutcome('skipped', repository.id, reason=str(exc))
        source_path = str(getattr(source_repo, 'local_path', '') or '').strip()
        if not source_path:
            return _SourceRepoOutcome(
                'skipped', repository.id,
                reason='inventory entry has no local_path '
                       '(REPOSITORY_ROOT_PATH not configured?)',
            )
        try:
            update_result = self._repository_service.update_source_to_task_branch(
                source_repo, branch_name,
            ) or {}
        except RuntimeError as exc:
            # ``update_source_to_task_branch`` raises with an
            # operator-readable message (dirty tree, fetch failed,
            # fast-forward refused, etc.). One-line warning, no traceback —
            # these are operator-state issues, not kato bugs.
            self.logger.warning(
                'update-source for task %s failed for repository %s: %s',
                task_id, repository.id, exc,
            )
            return _SourceRepoOutcome('failed', repository.id, error=str(exc))
        except Exception as exc:
            self.logger.exception(
                'update-source for task %s crashed in repository %s',
                task_id, repository.id,
            )
            return _SourceRepoOutcome('failed', repository.id, error=str(exc))
        warning = text_from_mapping(update_result, 'warning')
        warning_detail = None
        if warning:
            warning_detail = {
                'repository_id': repository.id,
                'warning': warning,
                # ``blocked`` means git refused and NOTHING was changed — the
                # operator has to look. The UI marks it with ⚠ rather than a
                # bullet.
                'blocked': bool(update_result.get('blocked', False)),
                'blocking_paths': list(update_result.get('blocking_paths') or []),
            }
        self.logger.info(
            'update-source for task %s: %s @ %s now on %s%s',
            task_id, repository.id, source_path, branch_name,
            f' ({warning})' if warning else '',
        )
        return _SourceRepoOutcome('updated', repository.id, warning=warning_detail)

    def configured_destination_branch(self, repository_id: str) -> str:
        """Branch the task was forked from for ``repository_id``, per kato config.

        This is the authoritative answer for the diff base in the
        Changes tab — kato always creates a task branch off this
        ref, so ``git diff <task_branch>...origin/<destination>``
        is what the operator wants to see. Auto-detecting via git
        (``origin/HEAD``) returns the *remote's* default, which
        is wrong whenever an operator has configured a non-default
        base (e.g. ``develop`` on Bitbucket).

        Returns '' when the inventory has no entry for the repo
        (unknown id) or when neither config nor inferred default
        is available — the webserver surfaces that as a precise
        operator-facing error instead of guessing.
        """
        normalized = str(repository_id or '').strip()
        if not normalized:
            return ''
        try:
            repository = self._repository_service.get_repository(normalized)
        except Exception:
            return ''
        try:
            return self._repository_service.destination_branch(repository) or ''
        except Exception:
            # ``destination_branch`` raises when no configured value
            # AND inference fails — safe to swallow here; '' means
            # "we don't know" and the caller emits the right
            # operator-facing error.
            return ''

    def push_task(self, task_id: str) -> dict[str, object]:
        """Commit + push the task branch for every repo in its workspace.

        Used by the planning UI's ``Push`` button: surfaces the work-in-
        progress branch on the remote without opening a pull request.
        Idempotent — pushes again from where the workspace currently is.
        """
        normalized = str(task_id or '').strip()
        if not normalized:
            return failure(
                'empty task id',
                flag='pushed',
                task_id=task_id,
            )
        # A repo tagged onto the task after the workspace was built is
        # only in the ticket's tags, not yet in the metadata this reads —
        # so fold the tags in FIRST or the newcomer is silently skipped.
        reconciled = self._reconcile_task_repositories(normalized)
        repos, branch_name_for_task, _task = self._resolve_publish_context(normalized)
        if not repos:
            return failure(
                'no workspace context for this task',
                flag='pushed',
                task_id=normalized,
            )
        pushed_repositories: list[str] = []
        skipped_repositories: list[dict[str, str]] = []
        failed_repositories: list[dict[str, str]] = []
        for repository in repos:
            branch_name = self._repository_service.build_branch_name(_task, repository)
            # Only act on repos that actually have unpushed work. The
            # ``Push`` button is enabled when *any* repo on the task
            # needs pushing — without this filter we would also call
            # ``publish_review_fix`` on the in-sync repos and trip
            # ``_assert_branch_checked_out`` (workspace on master) or
            # ``RepositoryHasNoChangesError`` for them.
            try:
                skip_reason = self._repository_service.push_skip_reason(
                    repository, branch_name,
                )
            except Exception:
                self.logger.exception(
                    'branch-needs-push pre-check failed for task %s repository %s',
                    normalized, repository.id,
                )
                skip_reason = 'push pre-check failed — see the kato log'
            if skip_reason:
                # The REAL reason, not a blanket "nothing to push": a
                # clone still sitting on master (the classic mid-task
                # repo) read exactly like one that was already in sync.
                self.logger.info(
                    'on-demand push for task %s: skipping %s — %s',
                    normalized, repository.id, skip_reason,
                )
                skipped_repositories.append({
                    'repository_id': repository.id,
                    'reason': skip_reason,
                })
                continue
            try:
                self._repository_service.publish_review_fix(
                    repository,
                    branch_name,
                    commit_message=f'Update {normalized}',
                )
                pushed_repositories.append(repository.id)
                self.logger.info(
                    'on-demand push for task %s: pushed branch %s to %s',
                    normalized, branch_name, repository.id,
                )
            except _ON_DEMAND_PUSH_EXPECTED_ERRORS as exc:
                # Race fallback: state changed between the pre-check
                # and the publish call (e.g. another agent pushed). One
                # warning line, no traceback.
                self.logger.warning(
                    'on-demand push for task %s skipped repository %s: %s',
                    normalized, repository.id, exc,
                )
                failed_repositories.append(
                    {'repository_id': repository.id, 'error': str(exc)},
                )
                continue
            except Exception as exc:
                self.logger.exception(
                    'on-demand push for task %s failed in repository %s',
                    normalized, repository.id,
                )
                failed_repositories.append(
                    {'repository_id': repository.id, 'error': str(exc)},
                )
        return {
            'pushed': bool(pushed_repositories),
            'task_id': normalized,
            # The task branch the work was pushed to — surfaced in the
            # Push toast so the operator sees "pushed … to branch <x>"
            # instead of a bare repo list.
            'branch': branch_name_for_task,
            'pushed_repositories': pushed_repositories,
            'skipped_repositories': skipped_repositories,
            'failed_repositories': failed_repositories,
            # Repos the tag reconcile pulled in on the way here, so the
            # toast can say the push covered a repo the operator only
            # just added.
            'synced_repositories': [
                str(r) for r in (reconciled.get('added_repositories') or []) if r
            ],
        }

    def pull_task(self, task_id: str) -> dict[str, object]:
        """Fast-forward every workspace clone of the task from its remote.

        Drives the planning UI's ``Pull`` button — symmetric to the
        ``Push`` button. Per-repo outcomes are surfaced so the
        operator sees exactly what happened (pulled, already in
        sync, refused for dirty tree, etc.) without having to look
        at logs.

        Returns:
            {
              'task_id': <id>,
              'pulled': bool,                # any repo actually moved
              'pulled_repositories': [{repository_id, commits_pulled}],
              'skipped_repositories': [{repository_id, reason, detail}],
              'failed_repositories':  [{repository_id, error}],
            }
        """
        normalized = str(task_id or '').strip()
        if not normalized:
            return failure(
                'empty task id',
                flag='pulled',
                task_id=task_id,
            )
        repos, branch_name, _task = self._resolve_publish_context(normalized)
        if not repos:
            return failure(
                'no workspace context for this task',
                flag='pulled',
                task_id=normalized,
            )
        pulled_repositories: list[dict[str, object]] = []
        skipped_repositories: list[dict[str, str]] = []
        failed_repositories: list[dict[str, str]] = []
        for repository in repos:
            repo_branch = self._repository_service.build_branch_name(_task, repository)
            try:
                outcome = self._repository_service.pull_workspace_clone(
                    repository, repo_branch,
                )
            except Exception as exc:
                self.logger.exception(
                    'on-demand pull for task %s failed in repository %s',
                    normalized, repository.id,
                )
                failed_repositories.append(
                    {'repository_id': repository.id, 'error': str(exc)},
                )
                continue
            if outcome.get('pulled') and outcome.get('updated'):
                pulled_repositories.append({
                    'repository_id': repository.id,
                    'commits_pulled': int(outcome.get('commits_pulled') or 0),
                })
                self.logger.info(
                    'on-demand pull for task %s: fast-forwarded %s by %s commit(s)',
                    normalized, repository.id, outcome.get('commits_pulled'),
                )
            elif outcome.get('pulled'):
                # ``pulled=True, updated=False`` — already in sync.
                skipped_repositories.append({
                    'repository_id': repository.id,
                    'reason': outcome.get('reason') or 'already_in_sync',
                    'detail': outcome.get('detail') or 'nothing to pull',
                })
            else:
                skipped_repositories.append({
                    'repository_id': repository.id,
                    'reason': outcome.get('reason') or 'unknown',
                    'detail': outcome.get('detail') or '',
                })
        return {
            'task_id': normalized,
            'pulled': bool(pulled_repositories),
            'pulled_repositories': pulled_repositories,
            'skipped_repositories': skipped_repositories,
            'failed_repositories': failed_repositories,
        }

    def merge_default_branch_for_task(self, task_id: str) -> dict[str, object]:
        """Fetch + merge each clone's default branch into its task branch.

        Drives the planning UI's ``Merge master`` button. The agent's
        clone can't run git itself (sandbox), so when a task branch
        drifts behind ``origin/<default>`` and conflicts, the agent
        is stuck. This does the merge on the operator's behalf; on
        conflict the markers are LEFT in the tree (not aborted) so
        the agent can resolve them by editing files.

        Returns:
            {
              'task_id': <id>,
              'merged': bool,                 # any repo cleanly merged
              'has_conflicts': bool,          # any repo left conflicted
              'merged_repositories':     [{repository_id, commits_merged}],
              'conflicted_repositories': [{repository_id, default_branch,
                                           conflicted_files: [...]}],
              'skipped_repositories':    [{repository_id, reason, detail}],
              'failed_repositories':     [{repository_id, error}],
            }
        """
        normalized = str(task_id or '').strip()
        if not normalized:
            return failure(
                'empty task id',
                flag='merged',
                task_id=task_id,
            )
        repos, _branch_name, task = self._resolve_publish_context(normalized)
        if not repos:
            return failure(
                'no workspace context for this task',
                flag='merged',
                task_id=normalized,
            )
        merged_repositories: list[dict[str, object]] = []
        conflicted_repositories: list[dict[str, object]] = []
        skipped_repositories: list[dict[str, str]] = []
        failed_repositories: list[dict[str, str]] = []
        for repository in repos:
            repo_branch = self._repository_service.build_branch_name(
                task, repository,
            )
            try:
                outcome = self._repository_service.merge_default_branch_into_clone(
                    repository, repo_branch,
                )
            except Exception as exc:
                self.logger.exception(
                    'merge-default for task %s failed in repository %s',
                    normalized, repository.id,
                )
                failed_repositories.append(
                    {'repository_id': repository.id, 'error': str(exc)},
                )
                continue
            if outcome.get('conflicts'):
                conflicted_repositories.append({
                    'repository_id': repository.id,
                    'default_branch': outcome.get('default_branch') or '',
                    'conflicted_files': list(
                        outcome.get('conflicted_files') or [],
                    ),
                })
                self.logger.info(
                    'merge-default for task %s: %s has %d conflicted file(s) '
                    'against %s — left in tree for the agent to resolve',
                    normalized, repository.id,
                    len(outcome.get('conflicted_files') or []),
                    outcome.get('default_branch'),
                )
            elif outcome.get('merged') and outcome.get('updated'):
                merged_repositories.append({
                    'repository_id': repository.id,
                    'commits_merged': int(outcome.get('commits_merged') or 0),
                    'default_branch': outcome.get('default_branch') or '',
                    'wip_committed': bool(outcome.get('wip_committed')),
                })
                self.logger.info(
                    'merge-default for task %s: merged %s into %s (%s commits)',
                    normalized, outcome.get('default_branch'),
                    repository.id, outcome.get('commits_merged'),
                )
            elif outcome.get('merged'):
                # merged=True, updated=False — already contained the
                # default branch, nothing to do.
                skipped_repositories.append({
                    'repository_id': repository.id,
                    'reason': 'already_up_to_date',
                    'detail': 'task branch already contains the default branch',
                })
            else:
                skipped_repositories.append({
                    'repository_id': repository.id,
                    'reason': outcome.get('reason') or 'unknown',
                    'detail': outcome.get('detail') or '',
                })
        return {
            'task_id': normalized,
            'merged': bool(merged_repositories),
            'has_conflicts': bool(conflicted_repositories),
            'merged_repositories': merged_repositories,
            'conflicted_repositories': conflicted_repositories,
            'skipped_repositories': skipped_repositories,
            'failed_repositories': failed_repositories,
        }

    def finalize_resolved_merges_for_task(self, task_id: str) -> dict[str, object]:
        """Commit any pending merge whose conflicts the agent has resolved.

        After ``merge_default_branch_for_task`` leaves a conflicted merge in
        the tree, the agent resolves it by editing files but can't run git,
        so the merge sits unfinished — the diff then shows every merged-in
        change, not just the branch's (the "hard to track" bug). Called
        opportunistically when the UI reads the Files/Changes view: kato
        finalises the merge (the completion of the operator's own "Merge
        master" click) the moment the markers are gone. A no-op — cheap
        ``MERGE_HEAD`` check — when nothing is pending, so it's safe to call
        on every read. Never raises; failures are logged and reported.

        Returns {'finalized_repositories': [ids], 'pending_repositories': [ids]}.
        """
        normalized = str(task_id or '').strip()
        finalized: list[str] = []
        pending: list[str] = []
        if not normalized:
            return {'finalized_repositories': finalized, 'pending_repositories': pending}
        try:
            repos, _branch_name, _task = self._resolve_publish_context(normalized)
        except Exception:
            return {'finalized_repositories': finalized, 'pending_repositories': pending}
        for repository in repos or []:
            try:
                outcome = self._repository_service.finalize_merge_if_resolved(repository)
            except Exception:
                self.logger.exception(
                    'finalize-merge for task %s failed in repository %s',
                    normalized, getattr(repository, 'id', '?'),
                )
                continue
            if outcome.get('finalized'):
                finalized.append(repository.id)
                self.logger.info(
                    'finalize-merge for task %s: committed the resolved merge '
                    'in %s', normalized, repository.id,
                )
            elif outcome.get('reason') == 'conflicts_remain':
                pending.append(repository.id)
        return {
            'finalized_repositories': finalized,
            'pending_repositories': pending,
        }

    def create_pull_request_for_task(self, task_id: str) -> dict[str, object]:
        """Open a PR for every repo of the task that doesn't already have one.

        Push happens as part of PR creation (the publication path stages,
        commits, and pushes before calling the host API). Repos that
        already have an open PR for this branch are skipped — surfaced
        in ``skipped_existing`` so the UI can show "PR already exists".
        """
        normalized = str(task_id or '').strip()
        if not normalized:
            return failure(
                'empty task id',
                flag='created',
                task_id=task_id,
            )
        # Same reason as push_task: open PRs for what the TAGS say the
        # task touches, not for a metadata snapshot taken before the
        # operator added a repo. Throttled, so the Done button (push →
        # PR) still pays for only one ticket lookup.
        self._reconcile_task_repositories(normalized)
        repos, _branch_name, task_obj = self._resolve_publish_context(normalized)
        if not repos:
            return failure(
                'no workspace context for this task',
                flag='created',
                task_id=normalized,
            )
        created: list[dict[str, str]] = []
        skipped: list[dict[str, str]] = []
        failed: list[dict[str, str]] = []
        for repository in repos:
            branch_name = self._repository_service.build_branch_name(task_obj, repository)
            try:
                existing = self._repository_service.find_pull_requests(
                    repository, source_branch=branch_name,
                )
            except Exception:
                self.logger.exception(
                    'on-demand PR for task %s: PR lookup failed in repository %s',
                    normalized, repository.id,
                )
                existing = []
            if existing:
                first = existing[0] if isinstance(existing[0], dict) else {}
                skipped.append({
                    'repository_id': repository.id,
                    'url': str(first.get('url', '') or ''),
                })
                continue
            try:
                # Reuse the canonical title builder so on-demand PRs
                # match the autonomous flow's format: ``<id> <summary>``,
                # not the older ``Implement <id>`` placeholder. Same
                # helper task_publisher uses for the auto-published
                # flow, so PR titles are consistent regardless of which
                # path opened them.
                from kato_core_lib.helpers.pull_request_utils import pull_request_title
                title = pull_request_title(task_obj)
                pull_request = self._repository_service.create_pull_request(
                    repository,
                    title=title,
                    source_branch=branch_name,
                    description=str(getattr(task_obj, 'summary', '') or ''),
                    commit_message=title,
                )
                created.append({
                    'repository_id': repository.id,
                    'url': str(pull_request.get('url', '') or ''),
                })
                self.logger.info(
                    'on-demand PR for task %s: opened %s in %s',
                    normalized, pull_request.get('url', ''), repository.id,
                )
            except _ON_DEMAND_PUSH_EXPECTED_ERRORS as exc:
                # "No changes to publish" race fallback — the workspace
                # state shifted between the pre-check and the create
                # call. One warning line, no traceback.
                self.logger.warning(
                    'on-demand PR for task %s skipped repository %s: %s',
                    normalized, repository.id, exc,
                )
                failed.append(
                    {'repository_id': repository.id, 'error': str(exc)},
                )
            except RuntimeError as exc:
                # The publish path raises bare RuntimeError for two
                # well-known cases: "expected branch X but found Y"
                # (workspace drift — handled by the boot-time realigner
                # and the diff-tab self-heal) and "remote rejected
                # ... reference already exists" (Git push of a branch
                # the remote already has at a different commit). Both
                # are operator-visible state issues, not kato bugs —
                # surface as a one-line warning, no stack trace.
                if 'expected repository' in str(exc) or 'reference already exists' in str(exc):
                    self.logger.warning(
                        'on-demand PR for task %s skipped repository %s: %s',
                        normalized, repository.id, exc,
                    )
                    failed.append(
                        {'repository_id': repository.id, 'error': str(exc)},
                    )
                else:
                    self.logger.exception(
                        'on-demand PR for task %s failed in repository %s',
                        normalized, repository.id,
                    )
                    failed.append(
                        {'repository_id': repository.id, 'error': str(exc)},
                    )
            except Exception as exc:
                self.logger.exception(
                    'on-demand PR for task %s failed in repository %s',
                    normalized, repository.id,
                )
                failed.append(
                    {'repository_id': repository.id, 'error': str(exc)},
                )
        return {
            'created': bool(created),
            'task_id': normalized,
            'created_pull_requests': created,
            'skipped_existing': skipped,
            'failed_repositories': failed,
        }

    def finish_task_planning_session(self, task_id: str) -> dict[str, object]:
        """Finalize a wait-planning chat task in one call.

        Equivalent to the operator clicking, in sequence: ``Push`` →
        ``Pull request`` → manually moving the ticket to In Review on
        the issue tracker. Idempotent: if everything is already pushed
        and the PR already exists, only the ticket-state move runs.
        Used by both the backend sentinel detector (Claude printed
        ``<KATO_TASK_DONE>``) and the planning UI's ``Done`` button.

        Returns a summary the UI can render — operator gets one
        notification per repo with what happened.
        """
        normalized = str(task_id or '').strip()
        if not normalized:
            return failure(
                'empty task id',
                flag='finished',
                task_id=task_id,
            )
        push_result = self.push_task(normalized)
        pr_result = self.create_pull_request_for_task(normalized)
        moved_to_review = False
        move_error = ''
        # False-success guard (mirrors task_publisher.py's NO_CHANGES
        # rule): push_task/create_pull_request_for_task never raise —
        # every per-repo failure is only recorded in their own
        # 'failed_repositories' lists. Without this check, a task whose
        # push AND PR creation both failed for every single repo (auth
        # expired, remote unreachable, branch protection, ...) still
        # moved to "In Review" unconditionally, implying a ready PR
        # exists when nothing actually reached the remote. Idempotent
        # no-ops (already pushed, PR already exists — no failures
        # recorded at all) and partial successes still proceed exactly
        # as before; this only blocks the all-repos-failed case.
        any_failure = bool(push_result.get('failed_repositories')) or bool(
            pr_result.get('failed_repositories'),
        )
        any_success = (
            bool(push_result.get('pushed_repositories'))
            or bool(pr_result.get('created_pull_requests'))
            or bool(pr_result.get('skipped_existing'))
        )
        if any_failure and not any_success:
            move_error = (
                'push and pull-request creation both failed for every '
                'repository; not moving to In Review'
            )
            self.logger.warning(
                'finish_task_planning_session: task %s had zero successes '
                '(push failures=%s, PR failures=%s) — NOT moving to In Review',
                normalized,
                push_result.get('failed_repositories'),
                pr_result.get('failed_repositories'),
            )
        else:
            try:
                self._task_state_service.move_task_to_review(normalized)
                moved_to_review = True
                self.logger.info(
                    'finished planning session for task %s: moved to In Review',
                    normalized,
                )
            except Exception as exc:
                move_error = str(exc) or exc.__class__.__name__
                # Full traceback to the kato terminal so the operator can
                # diagnose state-machine / auth / config issues. UI also
                # surfaces the message inline via the /finish response.
                self.logger.exception(
                    'failed to move task %s to In Review during finish',
                    normalized,
                )
        # Lesson capture (best-effort, non-blocking). When configured,
        # ``LessonsService`` extracts a one-line rule from the task and
        # writes it to the per-task lesson file. Runs in a background
        # thread so the finish call's response time isn't tied to an
        # LLM round-trip; failures stay inside the worker.
        self._kick_lesson_extraction(normalized, push_result, pr_result)
        return {
            'finished': moved_to_review,
            'task_id': normalized,
            'pushed': push_result,
            'pull_request': pr_result,
            'moved_to_review': moved_to_review,
            'move_error': move_error,
        }

    def _kick_lesson_extraction(
        self,
        task_id: str,
        push_result,
        pr_result,
    ) -> None:
        """Fire lesson extraction for a just-finished task in a worker thread.

        Context handed to the LLM is intentionally compact: task id,
        task summary (when retrievable), and a short trail of what
        publish did. The extractor is constrained to output a single
        concrete rule or NO_LESSON — long context isn't useful.
        """
        if self._lesson_service is None:
            return
        try:
            task = self._task_service.get_task(task_id)
            task_summary = str(getattr(task, 'summary', '') or '')
            task_description = str(getattr(task, 'description', '') or '')
        except Exception:
            task_summary = ''
            task_description = ''

        context_parts = [f'Task summary: {task_summary or "(none)"}']
        if task_description:
            context_parts.append(f'Task description:\n{task_description}')
        context_parts.append(f'Push result: {push_result!r}')
        context_parts.append(f'Pull request result: {pr_result!r}')
        task_context = '\n\n'.join(context_parts)

        self._lesson_service.capture_task_lesson(task_id, task_context)

    def _find_pull_requests_safe(self, repository, branch_name: str) -> list:
        """Direct PR-existence lookup for the publish-state check.

        ``task_publish_state`` is fetched on tab-load and after button
        clicks (NOT polled), so a direct provider call is fine — no cache,
        no background threads. Wrapped so a transient provider error (429,
        network) degrades to "no PR known" instead of failing the whole
        response: the git buttons gate on workspace presence and stay usable
        regardless of provider health. One warning line, never a traceback.
        """
        try:
            return self._repository_service.find_pull_requests(
                repository, source_branch=branch_name,
            ) or []
        except Exception as exc:
            self.logger.warning(
                'PR lookup failed for repository %s (branch %s): %s — '
                'treating as no PR',
                getattr(repository, 'id', ''), branch_name, exc,
            )
            return []

    def task_publish_state(self, task_id: str) -> dict[str, object]:
        """LOCAL, INSTANT git-button state — never touches the provider.

        Drives the ENABLED state of the planning UI's git buttons
        (Push / Pull / Merge / Pull request), which only need to know
        whether a workspace clone exists (+ whether there is local work to
        push). Deliberately does NO provider PR lookup: that call can sleep
        ~45-67s on a Bitbucket 429 retry backoff, and blocking a
        button-state fetch on it hangs the whole toolbar on "server isn't
        responding". PR existence is a SEPARATE, best-effort fetch
        (:meth:`task_pull_request_state`) that can be slow without freezing
        these buttons.

        - ``has_workspace=False`` → no workspace on disk yet; buttons stay
          disabled.
        - ``has_changes_to_push`` → any repo has unpushed work (dirty tree,
          branch never pushed, or local ahead of ``origin/<branch>``).

        All local git — returns in well under a second regardless of
        provider health. Best-effort: a per-repo push check failure is
        ignored so a transient git hiccup doesn't lock the buttons.
        """
        normalized = str(task_id or '').strip()
        if not normalized:
            return {'has_workspace': False, 'has_changes_to_push': False}
        repos, _branch_name, task_obj = self._resolve_publish_context(normalized)
        if not repos:
            return {'has_workspace': False, 'has_changes_to_push': False}
        has_changes_to_push = False
        for repository in repos:
            if has_changes_to_push:
                break
            branch_name = self._repository_service.build_branch_name(task_obj, repository)
            try:
                if self._repository_service.branch_needs_push(repository, branch_name):
                    has_changes_to_push = True
            except Exception:
                self.logger.exception(
                    'branch-needs-push check failed for task %s repository %s',
                    normalized, repository.id,
                )
        return {'has_workspace': True, 'has_changes_to_push': has_changes_to_push}

    def task_pull_request_state(self, task_id: str) -> dict[str, object]:
        """PR-existence for the task — a SEPARATE fetch from the git-button
        state so a slow provider can't freeze the toolbar.

        - ``has_pull_request`` → True only once EVERY repo on the task has
          an open PR (nothing left to publish). A repo added after the
          first PR round still has no PR, so this stays False and the Pull
          request button stays enabled until that repo is covered too (a
          task-wide "any repo has a PR" check previously left the button
          permanently disabled once the FIRST repo was published).
        - ``pull_request_urls`` → the existing PR URL(s), surfaced as the
          "open PR" link.

        Best-effort: a per-repo lookup failure degrades to "no PR"
        (:meth:`_find_pull_requests_safe`). Fetched on tab-load and on
        click — never polled — so its provider retry backoff never piles
        up and, being off the git-button path, never blocks those buttons.
        """
        normalized = str(task_id or '').strip()
        if not normalized:
            return {'has_pull_request': False, 'pull_request_urls': []}
        repos, _branch_name, task_obj = self._resolve_publish_context(normalized)
        if not repos:
            return {'has_pull_request': False, 'pull_request_urls': []}
        pull_request_urls: list[str] = []
        repos_missing_pull_request = 0
        for repository in repos:
            branch_name = self._repository_service.build_branch_name(task_obj, repository)
            existing = self._find_pull_requests_safe(repository, branch_name)
            if existing:
                first = existing[0] if isinstance(existing[0], dict) else {}
                url = str(first.get('url', '') or '')
                if url:
                    pull_request_urls.append(url)
            else:
                repos_missing_pull_request += 1
        return {
            'has_pull_request': repos_missing_pull_request == 0,
            'pull_request_urls': pull_request_urls,
        }

    def _resolve_publish_context(self, task_id: str):
        """Build (repos-with-local-path, branch_name, task-lite) for ``task_id``.

        Reads the workspace record + the inventory repositories, then
        rewrites ``local_path`` on each repo to its workspace clone path
        (the same shape :func:`provision_task_workspace_clones` produces
        for the autonomous flow). Returns ``([], '', None)`` whenever the
        task has no on-disk workspace — both UI buttons rely on that as
        the "disable everything" signal.
        """
        if self._workspace_manager is None:
            return [], '', None
        workspace = self._workspace_manager.get(task_id)
        if workspace is None:
            return [], '', None
        rewritten = []
        for repository_id in workspace.repository_ids:
            try:
                inventory_repo = self._repository_service.get_repository(repository_id)
            except ValueError:
                # Inventory lookup failed (e.g. REPOSITORY_ROOT_PATH points to a
                # missing directory). Build a minimal stub so git-only operations
                # (push, branch-check) still work. PR API calls need full credentials
                # and will fail gracefully in their own try/except blocks.
                clone_path = self._workspace_manager.repository_path(task_id, repository_id)
                clone_path_str = str(clone_path) if clone_path else ''
                if not clone_path_str:
                    self.logger.debug(
                        'workspace for task %s references unknown repository %s '
                        'and has no clone path; skipping',
                        task_id, repository_id,
                    )
                    continue
                self.logger.debug(
                    'workspace for task %s references unknown repository %s; '
                    'using workspace clone stub (inventory unavailable)',
                    task_id, repository_id,
                )
                rewritten.append(SimpleNamespace(id=repository_id, local_path=clone_path_str))
                continue
            clone_path = self._workspace_manager.repository_path(task_id, repository_id)
            rewritten_repo = copy.copy(inventory_repo)
            rewritten_repo.local_path = str(clone_path)
            rewritten.append(rewritten_repo)
        if not rewritten:
            return [], '', None
        task_lite = _PublishTaskLite(
            id=task_id, summary=str(workspace.task_summary or ''),
        )
        # build_branch_name only reads ``id`` / ``summary`` so the lite
        # object is a faithful stand-in here.
        branch_name = self._repository_service.build_branch_name(task_lite, rewritten[0])
        return rewritten, branch_name, task_lite
