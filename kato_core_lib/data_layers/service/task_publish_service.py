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

import threading

from agent_core_lib.agent_core_lib.helpers.project_conventions_utils import (
    repository_conventions_text,
)
from kato_core_lib.data_layers.data.fields import PullRequestFields
from kato_core_lib.data_layers.service.pull_request_lookup import LookupPaused
from kato_core_lib.data_layers.service.task_moves import (
    DONE,
    IN_REVIEW,
    OPERATOR_PRESSED_DONE,
    TaskMoves,
)
from kato_core_lib.helpers.deadline import run_all_with_deadline, run_with_deadline
from kato_core_lib.helpers.late_binding import provider_for
from kato_core_lib.helpers.publish_conventions_gate_utils import (
    publish_follows_project_conventions,
)
from kato_core_lib.helpers.service_results import failure
from kato_core_lib.helpers.logging_utils import configure_logger
from utils_core_lib.utils_core_lib.text_utils import text_from_mapping
import copy
from pathlib import Path
from types import SimpleNamespace
from dataclasses import dataclass
from kato_core_lib.data_layers.service.repository_service import (
    NOTHING_TO_PUSH,
    RepositoryHasNoChangesError,
)

_ON_DEMAND_PUSH_EXPECTED_ERRORS = (RepositoryHasNoChangesError,)

# How long the pre-push tag reconcile may take before publishing proceeds
# without it. It reads the ticket platform; a provider backoff must not hold
# an operator's git action.
_RECONCILE_TIMEOUT_SECONDS = 10.0

# How long the push-readiness scan may run before the git-button state is
# returned without it. Comfortably inside the client's own 8s abort, so a
# multi-repo task can never turn "is there a workspace?" into a failed fetch.
_PUSH_SCAN_BUDGET_SECONDS = 3.0

# How long the before-delete check may read every clone, all at once. Under
# the 20s the page waits for it (api.js); a clone that outruns it is reported
# "could not check", never "nothing to lose".
UNPUSHED_WORK_CHECK_SECONDS = 15.0


def _auth_hint_for(exc: Exception) -> str:
    """An actionable suffix for an auth failure, or ''.

    A bare ``401 Client Error: Unauthorized`` repeated once per repo per scan
    tells the operator nothing they can act on. Bitbucket's own body does:

        "API token must be used with an atlassian registered email"

    Atlassian API tokens authenticate as EMAIL + token; the older app
    passwords used USERNAME + password. kato prefers ``api_email`` and falls
    back to ``username``, so an operator who pasted a new API token without
    setting the email gets a guaranteed 401 on every call, with nothing
    naming the setting that fixes it.

    Read from the EXCEPTION TEXT rather than from config: the provider is the
    authority on why it refused, and inferring "you must be using an API
    token" from settings alone would misfire on a genuinely revoked app
    password.
    """
    text = str(exc or '')
    lowered = text.lower()
    if 'atlassian registered email' in lowered:
        return (
            ' — set BITBUCKET_API_EMAIL to the Atlassian account email that '
            'owns this token (an API token authenticates as EMAIL + token, '
            'not username + token)'
        )
    if 'api.github.com' in lowered or 'github.com' in lowered:
        return _github_permission_hint(lowered)
    if (
        'dev.azure.com' in lowered or '.visualstudio.com' in lowered
        or 'tf400813' in lowered  # Azure DevOps (incl. on-prem): "not authorized"
    ):
        return (
            ' — check AZURE_API_TOKEN: a personal access token for this '
            'organization with the Code (Read & Write) scope, not expired'
        )
    if '401' in text and 'unauthorized' in text.lower():
        return (
            ' — check the provider token, and that BITBUCKET_API_EMAIL is set '
            'when the token is an Atlassian API token'
        )
    return ''


def _github_permission_hint(lowered: str) -> str:
    """GitHub refuses on SCOPE far more often than on a bad token.

    And it hides the difference: a token that cannot write to a repository
    gets **404 Not Found** on ``POST /repos/…/pulls``, not 403 — GitHub
    will not confirm a repository exists to a caller that cannot see it. So
    the one error an operator is most likely to hit is also the one that
    reads as "kato is asking for the wrong URL".

    Reported as "for github it is not creating PRs at all by my token… it
    works on bitbucket" — a Bitbucket token needs no equivalent grant, so
    the same setup works there and silently does nothing here.
    """
    grants = (
        'a classic token needs the `repo` scope; a fine-grained token needs '
        'this repository selected plus Contents: Read and write, Pull '
        'requests: Read and write, and Metadata: Read'
    )
    if '404' in lowered:
        return (
            ' — GitHub answers 404 (not 403) when a token cannot WRITE to a '
            f'repository, so this is almost always a permissions gap: {grants}'
        )
    if '403' in lowered:
        return f' — the GitHub token is missing a permission: {grants}'
    if '401' in lowered:
        return (
            ' — the GitHub token was rejected outright: check GITHUB_API_TOKEN '
            'is set and has not expired'
        )
    return ''


def _is_permanent_auth_failure(exc) -> bool:
    """True for a refusal that will NEVER succeed on retry.

    Only 401/403. A 429 (Bitbucket rate limit — routine here) and any
    network blip stay transient and keep degrading quietly, because
    surfacing those would put a scary banner in front of the operator
    several times an hour for a condition that fixes itself.

    Text-matched off the exception, same reasoning as ``_auth_hint_for``:
    the provider is the authority on why it refused.
    """
    text = str(exc or '').lower()
    return (
        '401' in text and 'unauthorized' in text
    ) or (
        '403' in text and 'forbidden' in text
    )


@dataclass(frozen=True)
class _SourceRepoOutcome(object):
    """What updating ONE operator clone did: updated, skipped, blocked or failed.

    Returned per repository so the caller can bucket the run without a
    hundred-line loop appending into four lists as it goes.
    """

    kind: str
    repository_id: str
    reason: str = ''
    error: str = ''
    warning: dict | None = None


def _wanted_ids(repository_ids) -> set[str] | None:
    """The repositories asked for, lower-cased; None = every one."""
    if repository_ids is None:
        return None
    return {str(rid).strip().lower() for rid in repository_ids if str(rid).strip()}


def _only(repos: list, repository_ids) -> list:
    """``repos`` narrowed to ``repository_ids`` (None = all), case-blind."""
    wanted = _wanted_ids(repository_ids)
    if wanted is None:
        return repos
    return [repository for repository in repos if str(repository.id).lower() in wanted]


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


def _task_context_text(task) -> str:
    """What changed, for the conventions-aware writer. ``task`` may be the
    minimal ``_PublishTaskLite`` (no ``description``), so every field is
    read defensively."""
    task_id = str(getattr(task, 'id', '') or '')
    summary = str(getattr(task, 'summary', '') or '').strip()
    parts = [f'{task_id}: {summary}'] if (task_id or summary) else []
    description = str(getattr(task, 'description', '') or '').strip()
    if description:
        parts.append(description)
    return '\n\n'.join(parts)


def _push_problems(push_result: dict) -> dict[str, str]:
    """Why each repository's work did not reach the remote; empty when it all did.

    A failed push, and a push skipped for any reason but "nothing to push". The
    one answer for every caller that goes on to report or act on a push.
    """
    problems = {
        str(entry.get('repository_id')): str(entry.get('error') or '')
        for entry in push_result.get('failed_repositories') or []
    }
    for entry in push_result.get('skipped_repositories') or []:
        if entry.get('problem'):
            problems[str(entry.get('repository_id'))] = str(entry.get('reason') or '')
    return problems


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
        is_plan_source=None,
        plan_parent_of=None,
        publish_conventions_writer=None,
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
        self._get_publish_conventions_writer = provider_for(publish_conventions_writer)
        # ``task_id -> {status, error}``: tells the team the task is done (the
        # Slack post), set by the host once its announcer exists. None = nobody.
        self.announce_finished = None
        # The push-approval hold is SHARED with the host: the autonomous flow
        # parks a finished task there and the UI's approve button resumes it,
        # so both objects must see the same dict and the same lock.
        self._pending_publish = pending_publish if pending_publish is not None else {}
        self._pending_publish_lock = pending_publish_lock or threading.RLock()
        self._update_workspace_status_after_publish = update_workspace_status_after_publish
        # Folding the ticket's ``kato:repo:`` tags into the workspace metadata
        # belongs to the repositories subsystem; publishing only needs it run
        # before it reads that metadata. Best-effort by default.
        self._reconcile_task_repositories_impl = (
            reconcile_task_repositories or (lambda task_id: None)
        )
        # ``task_id -> bool``: was a plan split from this task? Its branch is
        # where the plan's sub-tasks merge, so its Pull merges a branch that
        # moved on both sides instead of refusing. None = no plans here.
        self._is_plan_source = is_plan_source or (lambda task_id: False)
        # ``task_id -> main task id`` ('' = no plan's sub-task): the task a
        # plan's sub-task was split from, whose branch gathers the plan's work.
        self._plan_parent_of = plan_parent_of or (lambda task_id: '')
        self._logger_getter = provider_for(
            logger if logger is not None else configure_logger('TaskPublishService'),
        )

    def _conventioned_commit_message(
        self, repos, *, context_text: str, fallback_commit_message: str,
    ) -> str:
        """``fallback_commit_message`` reshaped to the repositories' own
        CLAUDE.md/AGENTS.md, when the operator turned that on and at least
        one has one. See ``publish_conventions_writer.py`` — any failure
        there already falls back to this exact value."""
        writer = self._get_publish_conventions_writer()
        if writer is None or not publish_follows_project_conventions():
            return fallback_commit_message
        conventions = repository_conventions_text(repos or [])
        if not conventions:
            return fallback_commit_message
        return writer.write(
            context_text=context_text,
            conventions_text=conventions,
            fallback_commit_message=fallback_commit_message,
        ).commit_message

    def _conventioned_pr_text(
        self,
        repos,
        *,
        context_text: str,
        fallback_title: str,
        fallback_description: str,
        fallback_commit_message: str,
    ) -> tuple[str, str, str]:
        """``(title, description, commit_message)`` — same reasoning as
        ``_conventioned_commit_message``, for the PR-creation path."""
        fallback = (fallback_title, fallback_description, fallback_commit_message)
        writer = self._get_publish_conventions_writer()
        if writer is None or not publish_follows_project_conventions():
            return fallback
        conventions = repository_conventions_text(repos or [])
        if not conventions:
            return fallback
        written = writer.write(
            context_text=context_text,
            conventions_text=conventions,
            fallback_commit_message=fallback_commit_message,
            fallback_title=fallback_title,
            fallback_description=fallback_description,
        )
        return written.title, written.description, written.commit_message

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
        branch_name = self._repository_service.task_branch_name(task_id)
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


    def update_source_for_task(self, task_id: str, *, repository_ids=None) -> dict[str, object]:
        """Push + sync the operator's REPOSITORY_ROOT_PATH clones to the task branch.

        Drives the planning UI's "Update source" button. Pure git
        plumbing — no AI involvement. Two phases:

        1. ``push_task(task_id)`` — pushes the per-task workspace
           clone's branch to origin so the remote has the latest
           commits to pull from.
        2. For each repository the task touches, switch the operator's
           clone under ``REPOSITORY_ROOT_PATH`` to the task branch
           (see :meth:`_update_one_source_repository`).
        3. A plan's sub-task: every other repository — one the sub-task did
           not change, and one only its main task works in — goes to the MAIN
           task's branch, where the plan's merged work is, so the sub-task can
           be tested with the rest of the plan. It used to be skipped, and the
           source stayed wherever it was (master, or another task's branch).

        ``repository_ids``: only these repositories are pushed and switched —
        the Changes tab's per-repository button ("verify a small client change
        without my backend source moving while Docker builds from it"). Every
        other repository's source, and the sub-task main-branch pass for them,
        is left exactly as it is.

        Returns a per-repo summary the UI renders in the toast.
        """
        normalized = str(task_id or '').strip()
        if not normalized:
            return failure('empty task id', flag='updated', task_id=task_id)
        wanted = _wanted_ids(repository_ids)
        push_result = (
            self.push_task(normalized) if wanted is None
            else self.push_task(normalized, repository_ids=repository_ids)
        )
        # A repository whose work did not reach the remote is refused, not
        # updated: its source would switch to whatever origin has, and read
        # "updated" without the work. The others still go ahead, because
        # partial success is useful (a tester sees what made it to origin).
        push_problems = _push_problems(push_result)
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
        blocked_count = 0
        main_branch = self._main_branch_of(normalized)
        on_main_branch: list[str] = []
        outcomes: list[tuple['_SourceRepoOutcome', bool]] = []
        main_repository_ids = self._main_task_repository_ids(normalized) if main_branch else []
        known = {str(repository.id).lower() for repository in repos} | {rid.lower() for rid in main_repository_ids}
        for repository_id in sorted(wanted - known) if wanted is not None else []:
            outcomes.append((_SourceRepoOutcome(
                'failed', repository_id, error='not one of this task\'s repositories',
            ), False))
        repos = _only(repos, repository_ids)
        for repository in repos:
            push_problem = push_problems.get(str(repository.id))
            if push_problem is not None:
                outcomes.append((_SourceRepoOutcome(
                    'failed', repository.id,
                    error=f'not updated, because its work did not reach the remote: {push_problem}',
                ), False))
                continue
            outcome = self._update_one_source_repository(
                normalized,
                repository,
                self._repository_service.build_branch_name(task_obj, repository),
            )
            if outcome.kind == 'skipped' and main_branch:
                # Nothing of the sub-task's own here: the plan's work so far.
                outcomes.append((self._switch_source(
                    normalized, repository.id, main_branch, only_if_on_remote=True,
                ), True))
                continue
            outcomes.append((outcome, False))
        task_repository_ids = {str(rid).lower() for rid in self._all_repository_ids(normalized)}
        for repository_id in main_repository_ids:
            if wanted is not None and repository_id.lower() not in wanted:
                continue
            if repository_id.lower() not in task_repository_ids:
                outcomes.append((self._switch_source(
                    normalized, repository_id, main_branch, only_if_on_remote=True,
                ), True))
        for outcome, to_main_branch in outcomes:
            if outcome.warning is not None:
                warnings_per_repo.append(outcome.warning)
            if outcome.kind == 'updated' and to_main_branch:
                on_main_branch.append(outcome.repository_id)
            elif outcome.kind == 'updated':
                updated_repositories.append(outcome.repository_id)
            elif outcome.kind == 'blocked':
                blocked_count += 1
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
            normalized, len(updated_repositories) + len(on_main_branch), len(skipped_repositories),
            len(failed_repositories) + blocked_count,
        )
        return {
            'updated': bool(updated_repositories or on_main_branch),
            'task_id': normalized,
            'pushed': push_result,
            'updated_repositories': updated_repositories,
            # A sub-task's other repositories, now on its main task's branch.
            'main_branch': main_branch,
            'main_branch_repositories': on_main_branch,
            'skipped_repositories': skipped_repositories,
            'failed_repositories': failed_repositories,
            'warnings': warnings_per_repo,
        }

    def _all_repository_ids(self, task_id: str) -> list[str]:
        """Every repository of the task itself (not filtered)."""
        repos, _branch, _task = self._resolve_publish_context(task_id)
        return [str(repository.id) for repository in repos]

    def _main_branch_of(self, task_id: str) -> str:
        """The main task's branch when ``task_id`` is a plan's sub-task, else ''."""
        try:
            if not str(self._plan_parent_of(task_id) or '').strip():
                return ''
        except Exception:
            self.logger.exception('could not tell whether task %s is a plan sub-task', task_id)
            return ''
        return str(self._repository_service.plan_merge_branch(task_id) or '').strip()

    def _main_task_repository_ids(self, task_id: str) -> list[str]:
        """The repositories ``task_id``'s main task works in, as this kato has
        its workspace ([] when it has none here — another kato's plan)."""
        try:
            parent = str(self._plan_parent_of(task_id) or '').strip()
            workspace = self._workspace_manager.get(parent) if parent and self._workspace_manager else None
        except Exception:
            self.logger.exception('could not read the main task of %s', task_id)
            return []
        return [str(rid) for rid in (getattr(workspace, 'repository_ids', None) or []) if str(rid).strip()]

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
        return self._switch_source(task_id, repository.id, branch_name)

    def _switch_source(
        self, task_id: str, repository_id: str, branch_name: str, *, only_if_on_remote: bool = False,
    ) -> '_SourceRepoOutcome':
        """Switch the operator's clone of ``repository_id`` to ``branch_name``.

        ``only_if_on_remote``: a branch this run did not push (a plan's main
        branch) — skipped, with why, when the remote does not have it.
        """
        try:
            source_repo = self._repository_service.get_repository(repository_id)
        except ValueError as exc:
            # Failed, not skipped: there is work to bring over and kato could
            # not find where it goes. Reported as a skip this read "0 failed"
            # and a calm toast, on a source that was never updated.
            self.logger.warning(
                'update-source for task %s failed for repository %s: %s',
                task_id, repository_id, exc,
            )
            return _SourceRepoOutcome('failed', repository_id, error=str(exc))
        source_path = str(getattr(source_repo, 'local_path', '') or '').strip()
        if not source_path:
            return _SourceRepoOutcome(
                'failed', repository_id,
                error='inventory entry has no local_path '
                      '(REPOSITORY_ROOT_PATH not configured?)',
            )
        if only_if_on_remote:
            try:
                on_remote = self._repository_service.remote_has_branch(source_path, branch_name, source_repo)
            except Exception as exc:
                return _SourceRepoOutcome(
                    'failed', repository_id, error=f'could not ask the remote about {branch_name}: {exc}',
                )
            if not on_remote:
                return _SourceRepoOutcome(
                    'skipped', repository_id,
                    reason=f'{branch_name}, the main task\'s branch, is not on the remote yet',
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
                task_id, repository_id, exc,
            )
            return _SourceRepoOutcome('failed', repository_id, error=str(exc))
        except Exception as exc:
            self.logger.exception(
                'update-source for task %s crashed in repository %s',
                task_id, repository_id,
            )
            return _SourceRepoOutcome('failed', repository_id, error=str(exc))
        warning = text_from_mapping(update_result, 'warning')
        warning_detail = None
        if warning:
            warning_detail = {
                'repository_id': repository_id,
                'warning': warning,
                # ``blocked`` means git refused and NOTHING was changed — the
                # operator has to look. The UI marks it with ⚠ rather than a
                # bullet.
                'blocked': bool(update_result.get('blocked', False)),
                'blocking_paths': list(update_result.get('blocking_paths') or []),
            }
            if warning_detail['blocked']:
                self.logger.warning(
                    'update-source for task %s: %s was not updated: %s',
                    task_id, repository_id, warning,
                )
                return _SourceRepoOutcome('blocked', repository_id, warning=warning_detail)
        self.logger.info(
            'update-source for task %s: %s @ %s now on %s%s',
            task_id, repository_id, source_path, branch_name,
            f' ({warning})' if warning else '',
        )
        return _SourceRepoOutcome('updated', repository_id, warning=warning_detail)

    def configured_destination_branch(self, repository_id: str, local_path: str = '') -> str:
        """Branch the task was forked from for ``repository_id``, per kato config.

        With ``local_path`` (the task's clone): a plan sub-task's clone was
        forked from, and merges into, its plan's branch, so that is the answer.

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
            if local_path:
                return self._repository_service.destination_branch_of_clone(repository, local_path) or ''
            return self._repository_service.destination_branch(repository) or ''
        except Exception:
            # ``destination_branch`` raises when no configured value
            # AND inference fails — safe to swallow here; '' means
            # "we don't know" and the caller emits the right
            # operator-facing error.
            return ''

    def push_task(self, task_id: str, *, repository_ids=None) -> dict[str, object]:
        """Commit + push the task branch for every repo in its workspace.

        Used by the planning UI's ``Push`` button: surfaces the work-in-
        progress branch on the remote without opening a pull request.
        Idempotent — pushes again from where the workspace currently is.
        ``repository_ids``: only these (an Update source of one repository).
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
        repos = _only(repos, repository_ids)
        commit_message = self._conventioned_commit_message(
            repos, context_text=_task_context_text(_task),
            fallback_commit_message=f'Update {normalized}',
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
                # A clone that was never branch-prepped is RECOVERABLE, not a
                # dead end: the work is sitting in its working tree and the
                # only thing between it and a pull request is a checkout.
                # Try that once, then re-ask — the recovery refuses unless it
                # is provably safe, so a clone with its own commits still
                # falls through to the skip below with its reason intact.
                recovery_error = self._repository_service.recover_clone_onto_task_branch(
                    repository, branch_name,
                )
                if not recovery_error:
                    self.logger.info(
                        'on-demand push for task %s: moved %s onto %s and '
                        'retrying — it had never been branch-prepped',
                        normalized, repository.id, branch_name,
                    )
                    try:
                        skip_reason = self._repository_service.push_skip_reason(
                            repository, branch_name,
                        )
                    except Exception:
                        self.logger.exception(
                            'post-recovery push pre-check failed for task %s '
                            'repository %s', normalized, repository.id,
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
                    'problem': not skip_reason.startswith(NOTHING_TO_PUSH),
                })
                continue
            try:
                self._repository_service.publish_review_fix(
                    repository,
                    branch_name,
                    commit_message=commit_message,
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

    def pull_task(self, task_id: str, *, keep_conflicts: bool = True) -> dict[str, object]:
        """Bring every workspace clone of the task up to its remote branch.

        Drives the planning UI's ``Pull`` button — symmetric to the
        ``Push`` button. Per-repo outcomes are surfaced so the
        operator sees exactly what happened (pulled, already in
        sync, refused for dirty tree, etc.) without having to look
        at logs.

        A fast-forward, except on the task a plan was split from: its
        remote branch is where the sub-tasks' pull requests merge, so a
        clone that also has commits of its own (a ``Merge master``) merges
        origin's branch in instead of refusing (``pull_workspace_clone``).
        ``keep_conflicts=False`` is for a pull nobody pressed (the plan's
        own follow-up after a merge): a conflicting merge is undone there,
        never left half-done in the main task's clone.

        A pull that FAILED is a failure, never a skip: the toast's colour
        comes from these lists, and a failed fast-forward filed as a skip
        read as a green "Nothing to pull".

        Returns:
            {
              'task_id': <id>,
              'pulled': bool,                # any repo actually moved
              'pulled_repositories': [{repository_id, commits_pulled, merged}],
              'conflicted_repositories': [{repository_id, conflicted_files, branch}],
              'skipped_repositories': [{repository_id, reason, detail}],
              'failed_repositories':  [{repository_id, error, reason}],
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
        conflicted_repositories: list[dict[str, object]] = []
        skipped_repositories: list[dict[str, str]] = []
        failed_repositories: list[dict[str, str]] = []
        merge_when_diverged = self._plan_was_split_from(normalized)
        for repository in repos:
            repo_branch = self._repository_service.build_branch_name(_task, repository)
            try:
                outcome = self._repository_service.pull_workspace_clone(
                    repository, repo_branch,
                    merge_when_diverged=merge_when_diverged,
                    keep_conflicts=keep_conflicts,
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
                merged = bool(outcome.get('merged'))
                pulled_repositories.append({
                    'repository_id': repository.id,
                    'commits_pulled': int(outcome.get('commits_pulled') or 0),
                    'merged': merged,
                })
                self.logger.info(
                    'on-demand pull for task %s: %s %s by %s commit(s)',
                    normalized, 'merged origin into' if merged else 'fast-forwarded',
                    repository.id, outcome.get('commits_pulled'),
                )
            elif outcome.get('pulled'):
                # ``pulled=True, updated=False`` — already in sync.
                skipped_repositories.append({
                    'repository_id': repository.id,
                    'reason': outcome.get('reason') or 'already_in_sync',
                    'detail': outcome.get('detail') or 'nothing to pull',
                })
            elif outcome.get('conflicts'):
                conflicted_repositories.append({
                    'repository_id': repository.id,
                    'conflicted_files': list(outcome.get('conflicted_files') or []),
                    # What was merged in: ``origin/<branch>``, for the agent.
                    'branch': repo_branch,
                })
            elif outcome.get('reason') == 'dirty_working_tree':
                # A refusal to touch the agent's edits, not a failure: the
                # toast warns and says what to do.
                skipped_repositories.append({
                    'repository_id': repository.id,
                    'reason': 'dirty_working_tree',
                    'detail': outcome.get('detail') or '',
                })
            else:
                reason = outcome.get('reason') or 'unknown'
                failed_repositories.append({
                    'repository_id': repository.id,
                    'error': outcome.get('detail') or reason,
                    'reason': reason,
                })
        return {
            'task_id': normalized,
            'pulled': bool(pulled_repositories),
            'pulled_repositories': pulled_repositories,
            'conflicted_repositories': conflicted_repositories,
            'skipped_repositories': skipped_repositories,
            'failed_repositories': failed_repositories,
        }

    def _plan_was_split_from(self, task_id: str) -> bool:
        """Whether a plan was split from ``task_id``. Asking must never cost
        the pull: a queue that cannot be read means the plain fast-forward."""
        try:
            return bool(self._is_plan_source(task_id))
        except Exception:
            self.logger.exception('could not ask whether a plan was split from %s', task_id)
            return False

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

    def _empty_pull_request_reason(self, repository, branch_name: str) -> str:
        """Why a PR here would be empty — ``''`` when there is work to open.

        Delegated so a repository service that predates the check (or a test
        double) simply publishes as before, rather than failing.
        """
        probe = getattr(
            self._repository_service, 'pull_request_skip_reason', None,
        )
        if not callable(probe):
            return ''
        try:
            reason = probe(repository, branch_name)
        except Exception:
            # Never block a publish on the guard itself failing.
            self.logger.exception(
                'empty-PR check failed for repository %s', repository.id,
            )
            return ''
        # Only a real, non-empty STRING blocks a publish. A stub or a
        # service that answers with anything else (a bool, an object)
        # would otherwise read as truthy and silently suppress every
        # pull request — losing work, which is the one outcome this
        # guard must never cause.
        return reason.strip() if isinstance(reason, str) else ''

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
        # Reuse the canonical title builder so on-demand PRs match the
        # autonomous flow's format: ``<id> <summary>``, not the older
        # ``Implement <id>`` placeholder. Same helper task_publisher uses
        # for the auto-published flow, so PR titles are consistent
        # regardless of which path opened them.
        from kato_core_lib.helpers.pull_request_utils import pull_request_title
        fallback_title = pull_request_title(task_obj)
        title, description, commit_message = self._conventioned_pr_text(
            repos, context_text=_task_context_text(task_obj),
            fallback_title=fallback_title,
            fallback_description=str(getattr(task_obj, 'summary', '') or ''),
            fallback_commit_message=fallback_title,
        )
        created: list[dict[str, str]] = []
        skipped: list[dict[str, str]] = []
        failed: list[dict[str, str]] = []
        for repository in repos:
            branch_name = self._repository_service.build_branch_name(task_obj, repository)
            try:
                # Asked NOW, never a remembered answer: a stale "none" would
                # open a duplicate.
                existing = self._repository_service.find_pull_requests(
                    repository, source_branch=branch_name, cached=False,
                )
            except LookupPaused as paused:
                # "Could not ask" is not "none": opening one now could be a
                # duplicate. Said, and the operator presses again later.
                self.logger.warning(
                    'on-demand PR for task %s: %s: %s', normalized, repository.id, paused,
                )
                failed.append({'repository_id': repository.id, 'error': str(paused)})
                continue
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
            empty_reason = self._empty_pull_request_reason(
                repository, branch_name,
            )
            if empty_reason:
                # An EMPTY pull request — no files, no diff, nothing to
                # review — which the operator then has to notice and
                # decline, once per repo. Most visible on a multi-repo task
                # where one repo has the fix and the rest were already
                # merged: publishing re-opened a PR for every one of them.
                self.logger.info(
                    'PR for task %s: skipping repository %s — %s',
                    normalized, repository.id, empty_reason,
                )
                skipped.append({
                    'repository_id': repository.id,
                    'url': '',
                    'reason': empty_reason,
                })
                continue
            try:
                pull_request = self._repository_service.create_pull_request(
                    repository,
                    title=title,
                    source_branch=branch_name,
                    description=description,
                    commit_message=commit_message,
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

    def finish_task_planning_session(self, task_id: str, *, by_operator: bool = False) -> dict[str, object]:
        """Finalize a wait-planning chat task in one call.

        ``by_operator``: the operator pressed Done (or a plan board card's
        Done — the same call). A plan's sub-task then goes to the tracker's
        DONE, not In Review: its board counts it finished once its pull
        request is merged into the main task's branch, and the tasks after it
        start then. The agent's ``<KATO_TASK_DONE>`` never closes a ticket.

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
        moved_to_done = False
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
        any_failure = bool(_push_problems(push_result)) or bool(
            pr_result.get('failed_repositories'),
        )
        # ``skipped_existing`` also holds repositories left out because a
        # pull request would be empty (those carry a ``reason``). Only a pull
        # request that really exists is a success: the empty kind let Done move
        # a ticket to In Review with nothing pushed anywhere.
        any_success = (
            bool(push_result.get('pushed_repositories'))
            or bool(pr_result.get('created_pull_requests'))
            or any(
                not entry.get('reason')
                for entry in pr_result.get('skipped_existing') or []
            )
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
            moves = TaskMoves(self._task_state_service, logger=self.logger)
            try:
                if by_operator and self._is_plan_subtask(normalized):
                    # The one move to done — every observer of "done" hears it
                    # (release notes, Slack, containers, the plan board).
                    moves.move(normalized, DONE, OPERATOR_PRESSED_DONE)
                    moved_to_done = True
                else:
                    moves.move(normalized, IN_REVIEW, OPERATOR_PRESSED_DONE)
                    moved_to_review = True
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
        finished = moved_to_review or moved_to_done
        if finished:
            # A task kato ran by itself parked its work for "Approve push"; it
            # has just been published, so there is nothing left to approve.
            with self._pending_publish_lock:
                self._pending_publish.pop(normalized, None)
        return {
            'finished': finished,
            'task_id': normalized,
            'pushed': push_result,
            'pull_request': pr_result,
            'moved_to_review': moved_to_review,
            'moved_to_done': moved_to_done,
            'move_error': move_error,
            # What the team was told (the Slack post), for the Done toast.
            'slack': self._announce_finished(normalized) if finished else None,
        }

    def _is_plan_subtask(self, task_id: str) -> bool:
        """A task that runs one of a plan's steps (it merges into its main
        task's branch)."""
        try:
            return bool(str(self._get_repository_service().plan_merge_branch(task_id) or '').strip())
        except Exception:
            return False

    def _announce_finished(self, task_id: str) -> dict | None:
        announce = self.announce_finished
        if announce is None:
            return None
        try:
            return announce(task_id)
        except Exception as exc:
            self.logger.exception('could not tell anyone task %s is done', task_id)
            return {'status': 'failed', 'error': str(exc) or exc.__class__.__name__}

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

    def _find_pull_requests_safe(self, repository, branch_name: str):
        """``(pull_requests, permanent_error)`` for the publish-state check.

        ``task_publish_state`` is fetched on tab-load and after button
        clicks (NOT polled). The answer may be remembered
        (``PullRequestLookup``, cleared the moment kato opens a pull request),
        so a many-repository task's tab does not cost a call per repository
        every time it opens, and a host that said 429 is not asked again until
        its pause ends. Wrapped so a transient provider error (429,
        network) degrades to "no PR known" instead of failing the whole
        response: the git buttons gate on workspace presence and stay usable
        regardless of provider health. One warning line, never a traceback.

        A 401/403 is NOT that kind of error, and collapsing it into "no PR"
        is a lie the operator acts on. It never heals, so every later fetch
        repeats it; the UI then shows "no pull request" for a branch that may
        well have one, and offers to open a duplicate. The one report of this
        was a shared Bitbucket token used without ``BITBUCKET_API_EMAIL`` —
        an Atlassian API token authenticates as EMAIL + token, so every call
        401s forever while the UI says the branch simply has no PR.

        So a PERMANENT failure comes back as text for the caller to surface,
        while transient ones keep degrading silently as before. Either way a
        failed lookup returns ``None`` for the pull requests, not ``[]``: the
        per-repository list must say "couldn't check", never "no pull request".
        """
        try:
            return self._repository_service.find_pull_requests(
                repository, source_branch=branch_name,
            ) or [], ''
        except Exception as exc:
            hint = _auth_hint_for(exc)
            self.logger.warning(
                'PR lookup failed for repository %s (branch %s): %s — '
                'treating as no PR%s',
                getattr(repository, 'id', ''), branch_name, exc,
                hint,
            )
            if _is_permanent_auth_failure(exc):
                repo_id = str(getattr(repository, 'id', '') or 'repository')
                return None, (
                    f'Cannot read pull requests for {repo_id}: the provider '
                    f'rejected kato\'s credentials ({exc}){hint}. '
                    f'Pull-request status below may be wrong until this is '
                    f'fixed.'
                )
            return None, ''

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
        - ``merge_branch`` → for a plan's sub-task, the branch it merges
          into (its main task's), named in the git buttons' tooltips.

        All local git — returns in well under a second regardless of
        provider health. Best-effort: a per-repo push check failure is
        ignored so a transient git hiccup doesn't lock the buttons.
        """
        normalized = str(task_id or '').strip()
        if not normalized:
            return {'has_workspace': False, 'has_changes_to_push': False}
        repos, task_branch_name, task_obj = self._resolve_publish_context(normalized)
        if not repos:
            return {'has_workspace': False, 'has_changes_to_push': False}
        # ``has_workspace`` is the answer the BUTTONS gate on, and it is already
        # decided — the workspace record either exists or it does not. Nothing
        # below may hold it up.
        #
        # ``has_changes_to_push`` is a different kind of answer: one git
        # subprocess per repository. "Local git, well under a second" was true
        # for a two-repo task and false for a real one — a 25-repo task
        # measured 3.6-4.9s here, against a client that aborts at 8s. Past that
        # the fetch fails, and a task whose workspace is plainly on disk
        # reports "the server isn't responding" and disables every git button.
        #
        # So the scan gets a BUDGET. Whatever it has decided when the budget
        # runs out is what we return: False simply means "no unpushed work
        # found (yet)", which costs a tooltip nuance, never a disabled button.
        def _scan_for_unpushed_work() -> bool:
            for repository in repos:
                branch_name = self._repository_service.build_branch_name(
                    task_obj, repository,
                )
                try:
                    if self._repository_service.branch_needs_push(repository, branch_name):
                        return True
                except Exception:
                    self.logger.exception(
                        'branch-needs-push check failed for task %s repository %s',
                        normalized, repository.id,
                    )
            return False

        has_changes_to_push = run_with_deadline(
            _scan_for_unpushed_work,
            seconds=_PUSH_SCAN_BUDGET_SECONDS,
            default=False,
            on_timeout=lambda: self.logger.warning(
                'push-readiness scan for task %s exceeded %.1fs across %d '
                'repositor%s; reporting "no changes to push" so the git '
                'buttons still enable',
                normalized, _PUSH_SCAN_BUDGET_SECONDS, len(repos),
                'y' if len(repos) == 1 else 'ies',
            ),
        )
        return {
            'has_workspace': True,
            'has_changes_to_push': has_changes_to_push,
            'branch': task_branch_name,
            # A plan sub-task's git actions use the branch of the task it was
            # split from where any other task uses master ('' = not one). No git.
            'merge_branch': self._repository_service.plan_merge_branch(normalized),
        }

    def task_unpushed_work(self, task_id: str) -> dict[str, object]:
        """What deleting this task's workspace would lose, per repository.

        Asked before the delete dialog opens. Unlike ``has_changes_to_push``
        (a button hint with a 3s budget, where False means "none found yet")
        this answer is a warning about lost work, so it never guesses:

        - ``repositories`` — each clone holding work its remote does not
          have: ``changed_files`` (uncommitted, on any branch) and
          ``unpushed_commits`` (on any local branch, pushed nowhere).
        - ``unchecked`` — each clone git could not read in time, with why.
          The page treats it as "may lose work", never as "clean".

        A repository with no clone on disk has nothing to lose and is left
        out. All local git, every clone at once.
        """
        normalized = str(task_id or '').strip()
        empty = {'repositories': [], 'unchecked': []}
        if not normalized:
            return empty
        repos, _branch_name, _task = self._resolve_publish_context(normalized)
        clones = [
            repository for repository in repos
            if (Path(str(getattr(repository, 'local_path', '') or '')) / '.git').is_dir()
        ]
        if not clones:
            return empty
        late = object()
        results = run_all_with_deadline(
            [
                (lambda path=str(repository.local_path): self._unpushed_work_of(path))
                for repository in clones
            ],
            seconds=UNPUSHED_WORK_CHECK_SECONDS,
            default=late,
        )
        repositories: list[dict[str, object]] = []
        unchecked: list[dict[str, object]] = []
        for repository, result in zip(clones, results):
            repository_id = str(getattr(repository, 'id', '') or '')
            if result is late:
                unchecked.append({
                    'repository_id': repository_id,
                    'error': f'git did not answer within {UNPUSHED_WORK_CHECK_SECONDS:g}s',
                })
                continue
            if 'error' in result:
                unchecked.append({'repository_id': repository_id, 'error': result['error']})
                continue
            changed_files = len(result['changed_files'])
            unpushed_commits = int(result['unpushed_commits'])
            if changed_files or unpushed_commits:
                repositories.append({
                    'repository_id': repository_id,
                    'changed_files': changed_files,
                    'unpushed_commits': unpushed_commits,
                })
        return {'repositories': repositories, 'unchecked': unchecked}

    def _unpushed_work_of(self, local_path: str) -> dict[str, object]:
        """One clone's answer; a git failure comes back as its ``error``
        (``run_all_with_deadline`` would turn a raise into "late")."""
        try:
            return self._repository_service.work_not_on_remote(local_path)
        except Exception as exc:
            self.logger.warning('could not read unpushed work in %s: %s', local_path, exc)
            return {'error': str(exc) or type(exc).__name__}

    # How long the pull-request lookup may take, all repositories at once —
    # under the 8s the page waits for it (api.js), so a slow git host costs
    # its repository's answer ("could not check"), never the whole list.
    PULL_REQUEST_LOOKUP_SECONDS = 6.0

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
        - ``repositories`` → per repository on the task, in order: its
          ``repository_id``, its open ``pull_request`` (``id`` / ``title`` /
          ``url``, or None) and ``lookup_failed`` — the provider could not be
          asked, so "no pull request" would be a guess. The header's PR list,
          so none is missed (to merge, or to hand to reviewers).

        Best-effort: a per-repo lookup failure degrades to "no PR"
        (:meth:`_find_pull_requests_safe`). Fetched on tab-load and on
        click — never polled — so its provider retry backoff never piles
        up and, being off the git-button path, never blocks those buttons.
        """
        normalized = str(task_id or '').strip()
        if not normalized:
            return {'has_pull_request': False, 'pull_request_urls': [], 'repositories': []}
        repos, _branch_name, task_obj = self._resolve_publish_context(normalized)
        if not repos:
            return {'has_pull_request': False, 'pull_request_urls': [], 'repositories': []}
        pull_request_urls: list[str] = []
        repositories: list[dict[str, object]] = []
        repos_missing_pull_request = 0
        # First permanent failure only. One bad credential fails every repo
        # identically, and 25 copies of the same sentence is not a better
        # report than one.
        lookup_error = ''
        branches = [
            self._repository_service.build_branch_name(task_obj, repository)
            for repository in repos
        ]
        # Every repository at once, within the lookup's time: one asked after
        # another, a task with many repositories (or one slow host) outran the
        # page's wait and the whole list failed.
        late = object()
        results = run_all_with_deadline(
            [
                (lambda repository=repository, branch=branch:
                    self._find_pull_requests_safe(repository, branch))
                for repository, branch in zip(repos, branches)
            ],
            seconds=self.PULL_REQUEST_LOOKUP_SECONDS,
            default=late,
        )
        for repository, branch, result in zip(repos, branches, results):
            if result is late:
                self.logger.warning(
                    'PR lookup for repository %s did not answer within %ss — '
                    'shown as not checked',
                    getattr(repository, 'id', ''), self.PULL_REQUEST_LOOKUP_SECONDS,
                )
                result = (None, '')
            existing, failure = result
            if failure and not lookup_error:
                lookup_error = failure
            pull_request = None
            if existing:
                first = existing[0] if isinstance(existing[0], dict) else {}
                pull_request = {
                    field: str(first.get(field, '') or '')
                    for field in (PullRequestFields.ID, PullRequestFields.TITLE, PullRequestFields.URL)
                }
                if pull_request[PullRequestFields.URL]:
                    pull_request_urls.append(pull_request[PullRequestFields.URL])
            else:
                repos_missing_pull_request += 1
            entry = {
                'repository_id': str(getattr(repository, 'id', '') or ''),
                'pull_request': pull_request,
                'lookup_failed': existing is None,
            }
            if existing is None:
                # Not asked, so the row still leads to the pull request: the
                # host's own page of the branch's pull requests.
                entry['pull_requests_page_url'] = self._pull_requests_page(repository, branch)
            repositories.append(entry)
        return {
            'has_pull_request': repos_missing_pull_request == 0,
            'pull_request_urls': pull_request_urls,
            'repositories': repositories,
            # Carried alongside, never INSTEAD of, the answer: the git
            # buttons must stay usable when the provider is unreachable
            # (see task_publish_state), so this is a note, not a failure.
            'lookup_error': lookup_error,
        }

    def _pull_requests_page(self, repository, branch: str) -> str:
        """The host's page of ``branch``'s pull requests ('' = no address).
        Built from the remote, so it needs no answer from the host, and it never
        costs the lookup it is part of."""
        try:
            url = self._repository_service.branch_pull_requests_url(repository, branch)
        except Exception:
            self.logger.exception(
                'could not build the pull requests page of repository %s',
                getattr(repository, 'id', ''),
            )
            return ''
        return url if isinstance(url, str) else ''

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
