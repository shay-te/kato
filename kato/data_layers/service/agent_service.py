from __future__ import annotations

import logging

from core_lib.data_layers.service.service import Service

from kato.data_layers.service.agent_state_registry import AgentStateRegistry
from kato.data_layers.service.task_failure_handler import TaskFailureHandler
from kato.data_layers.service.review_comment_service import ReviewCommentService
from kato.data_layers.service.task_publisher import TaskPublisher
from kato.data_layers.service.task_state_service import TaskStateService
from kato.validation.repository_connections import (
    RepositoryConnectionsValidator,
)
from kato.validation.startup_dependency_validator import (
    StartupDependencyValidator,
)
from kato.helpers.logging_utils import configure_logger
from kato.helpers.mission_logging_utils import log_mission_step
from kato.data_layers.data.task import Task
from kato.data_layers.service.implementation_service import ImplementationService
from kato.helpers.task_context_utils import PreparedTaskContext, session_suffix
from kato.data_layers.service.notification_service import NotificationService
from kato.data_layers.service.repository_service import RepositoryService
from kato.data_layers.service.task_preflight_service import (
    TaskPreflightService,
)
from kato.data_layers.service.task_service import TaskService
from kato.data_layers.service.testing_service import TestingService
from kato.data_layers.data.fields import ImplementationFields, TaskCommentFields, TaskTags
from kato.data_layers.data.review_comment import ReviewComment
from kato.validation.branch_publishability import (
    TaskBranchPublishabilityValidator,
)
from kato.validation.branch_push import TaskBranchPushValidator
from kato.validation.model_access import TaskModelAccessValidator
from kato.helpers.task_execution_utils import (
    apply_testing_message,
    implementation_succeeded,
    skip_task_result,
    testing_failed_result,
    testing_succeeded,
)


class AgentService(Service):
    """Orchestrate the end-to-end task workflow and delegate specialized work to collaborators."""
    # NOTE: Task and review coordination state is kept in memory only.
    # It is not durable across process restarts.
    def __init__(
        self,
        task_service: TaskService,
        task_state_service: TaskStateService,
        implementation_service: ImplementationService,
        testing_service: TestingService,
        repository_service: RepositoryService,
        notification_service: NotificationService,
        state_registry: AgentStateRegistry | None = None,
        review_comment_service: ReviewCommentService | None = None,
        task_failure_handler: TaskFailureHandler | None = None,
        task_publisher: TaskPublisher | None = None,
        repository_connections_validator: RepositoryConnectionsValidator | None = None,
        startup_validator: StartupDependencyValidator | None = None,
        task_preflight_service: TaskPreflightService | None = None,
        skip_testing: bool = False,
        planning_session_runner=None,
        session_manager=None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.logger = logger or configure_logger(self.__class__.__name__)
        if task_service is None:
            raise ValueError('task_service is required')
        if task_state_service is None:
            raise ValueError('task_state_service is required')
        if implementation_service is None:
            raise ValueError('implementation_service is required')
        if testing_service is None:
            raise ValueError('testing_service is required')
        if repository_service is None:
            raise ValueError('repository_service is required')
        if notification_service is None:
            raise ValueError('notification_service is required')
        if review_comment_service is not None:
            review_state_registry = review_comment_service.state_registry
            if state_registry is not None and review_state_registry is not state_registry:
                raise ValueError(
                    'state_registry must match review_comment_service.state_registry'
                )
            state_registry = state_registry or review_state_registry
        self._task_service = task_service
        self._task_state_service = task_state_service
        self._implementation_service = implementation_service
        self._testing_service = testing_service
        self._repository_service = repository_service
        self._notification_service = notification_service
        self._skip_testing = bool(skip_testing)
        self._planning_session_runner = planning_session_runner
        self._session_manager = session_manager
        self._state_registry = state_registry or AgentStateRegistry()
        self._review_comment_service = review_comment_service or ReviewCommentService(
            self._task_service,
            self._implementation_service,
            self._repository_service,
            self._state_registry,
        )
        self._repository_connections_validator = (
            repository_connections_validator
            or RepositoryConnectionsValidator(self._repository_service)
        )
        self._task_failure_handler = task_failure_handler or TaskFailureHandler(
            self._task_service,
            self._task_state_service,
            self._repository_service,
            self._notification_service,
        )
        self._startup_validator = startup_validator or StartupDependencyValidator(
            self._repository_connections_validator,
            self._task_service,
            self._implementation_service,
            self._testing_service,
            self._skip_testing,
        )
        self._task_preflight_service = task_preflight_service or TaskPreflightService(
            task_model_access_validator=TaskModelAccessValidator(
                self._implementation_service,
            ),
            task_service=self._task_service,
            repository_service=self._repository_service,
            task_branch_push_validator=TaskBranchPushValidator(
                self._repository_service,
            ),
            task_branch_publishability_validator=TaskBranchPublishabilityValidator(
                self._repository_service,
            ),
        )
        self._task_publisher = task_publisher or TaskPublisher(
            self._task_service,
            self._task_state_service,
            self._repository_service,
            self._notification_service,
            self._state_registry,
            self._task_failure_handler,
        )

    @property
    def notification_service(self) -> NotificationService:
        return self._notification_service

    def validate_connections(self) -> None:
        self._startup_validator.validate(self.logger)

    def shutdown(self) -> None:
        """Stop all active OpenHands conversations to remove agent-server containers."""
        self._implementation_service.stop_all_conversations()
        self._testing_service.stop_all_conversations()
        if self._session_manager is not None:
            try:
                self._session_manager.shutdown()
            except Exception:
                self.logger.exception('error tearing down planning sessions on shutdown')

    def get_assigned_tasks(self) -> list[Task]:
        return self._task_service.get_assigned_tasks()

    def get_new_pull_request_comments(self) -> list[ReviewComment]:
        self._cleanup_done_task_conversations()
        return self._review_comment_service.get_new_pull_request_comments()

    def _cleanup_done_task_conversations(self) -> None:
        """Delete conversation containers for tasks no longer in the review state.

        When a reviewer merges a PR and moves the task to done, Kato detects
        it is missing from the review-task list and removes the associated
        agent-server container to avoid accumulation.
        """
        try:
            current_review_task_ids = {
                str(task.id) for task in self._task_service.get_review_tasks()
            }
        except Exception:
            self.logger.warning(
                'failed to fetch review tasks for conversation cleanup; skipping'
            )
            return

        stale_task_ids = self._state_registry.tracked_task_ids() - current_review_task_ids
        for task_id in stale_task_ids:
            for session_id in self._state_registry.session_ids_for_task(task_id):
                self.logger.info(
                    'task %s is no longer in review; stopping conversation %s',
                    task_id,
                    session_id,
                )
                try:
                    self._implementation_service.delete_conversation(session_id)
                except Exception:
                    self.logger.warning(
                        'failed to stop conversation %s for done task %s',
                        session_id,
                        task_id,
                    )
            self._state_registry.forget_task(task_id)

        self._cleanup_done_planning_sessions(current_review_task_ids)

    def _cleanup_done_planning_sessions(
        self,
        current_review_task_ids: set[str],
    ) -> None:
        """Drop planning-UI tabs whose ticket has moved to done/closed.

        A streaming session's record is kept on disk while the ticket is in
        ``Open`` or in the configured review states. As soon as the ticket
        leaves both buckets — i.e. the reviewer marked it done or closed —
        we terminate the live subprocess (if any) and delete the persisted
        record so the tab disappears from the planning UI.
        """
        if self._session_manager is None:
            return
        try:
            current_assigned_task_ids = {
                str(task.id) for task in self._task_service.get_assigned_tasks()
            }
        except Exception:
            self.logger.warning(
                'failed to fetch assigned tasks for session cleanup; '
                'leaving planning sessions in place this cycle'
            )
            return

        live_task_ids = current_assigned_task_ids | current_review_task_ids
        try:
            records = self._session_manager.list_records()
        except Exception:
            self.logger.exception('failed to list planning session records')
            return
        for record in records:
            if record.task_id in live_task_ids:
                continue
            self.logger.info(
                'task %s is no longer assigned or in review; closing planning session tab',
                record.task_id,
            )
            try:
                self._session_manager.terminate_session(
                    record.task_id,
                    remove_record=True,
                )
            except Exception:
                self.logger.exception(
                    'failed to close planning session for task %s',
                    record.task_id,
                )

    def handle_pull_request_comment(self, payload: dict) -> dict[str, str]:
        return self._review_comment_service.handle_pull_request_comment(payload)

    def process_review_comment(self, comment: ReviewComment) -> dict[str, str]:
        return self._review_comment_service.process_review_comment(comment)

    def process_assigned_task(self, task: Task) -> dict[str, object] | None:
        # No in-memory "already processed" short-circuit. The ticket system
        # (state + comments) is the single source of truth: successful tasks
        # have already been moved out of the scanned states, and skipped/
        # failed tasks carry comments that the gate and preflight read fresh
        # on every scan. Remove the comment, the task is re-evaluated.

        # `kato:wait-planning` short-circuits the orchestration: register the
        # planning tab so the human can chat with the agent in the UI, but
        # do *no* implementation, testing, or publishing work. The user
        # controls the conversation; remove the tag whenever they want
        # autonomous execution to take over.
        planning_only_result = self._handle_wait_for_planning(task)
        if planning_only_result is not None:
            return planning_only_result

        prepared_task = self._task_preflight_service.prepare_task_execution_context(
            task,
            task_failure_handler=self._task_failure_handler.handle_task_failure,
            repository_resolution_failure_handler=(
                self._task_failure_handler.handle_repository_resolution_failure
            ),
            repository_preparation_failure_handler=self._task_failure_handler.handle_task_failure,
            task_definition_failure_handler=(
                self._task_failure_handler.handle_task_definition_failure
            ),
            branch_preparation_failure_handler=self._task_failure_handler.handle_task_failure,
            branch_push_failure_handler=self._task_failure_handler.handle_started_task_failure,
        )
        if prepared_task is None or isinstance(prepared_task, dict):
            return prepared_task

        if not self._start_task_processing(task, prepared_task):
            return None
        execution = self._run_task_implementation(task, prepared_task)
        if execution is None:
            return None
        testing_succeeded, testing_result, execution = self._run_task_testing_validation(
            task,
            prepared_task,
            execution,
        )
        if not testing_succeeded:
            return testing_result
        return self._task_publisher.publish_task_execution(task, prepared_task, execution)

    def _handle_wait_for_planning(self, task: Task) -> dict[str, object] | None:
        """If the task is tagged ``kato:wait-planning``, register the chat tab and stop.

        The orchestrator does no implementation/testing/publishing work for
        these tasks — the human drives the conversation in the UI. Remove
        the tag from the ticket whenever you want the agent to take over
        autonomously. We register the session here (so the tab shows up
        the moment the scan picks the task up) but never block on it.
        """
        if not self._task_has_wait_planning_tag(task):
            return None
        if self._session_manager is None:
            # No streaming backend (e.g. OpenHands) — nothing to register.
            # The task simply isn't worked on; nothing to comment either.
            self.logger.info(
                'task %s has %s but the active backend has no streaming UI; skipping',
                task.id,
                TaskTags.WAIT_PLANNING,
            )
            return skip_task_result(task.id, [])
        # Already-alive session → tab is up, subprocess is waiting for the
        # user. Skip silently so the scan loop doesn't spam the terminal
        # every cycle while the user is mid-conversation.
        existing = self._session_manager.get_session(str(task.id))
        if existing is not None and existing.is_alive:
            return skip_task_result(task.id, [])
        cwd, expected_branch = self._resolve_wait_planning_context(task)
        # Belt-and-suspenders: the prompt explicitly forbids tool use,
        # AND the CLI runs in ``--permission-mode plan`` so Claude can't
        # execute even if it tries. If the user later removes the
        # ``kato:wait-planning`` tag, the autonomous flow uses the
        # configured permission mode instead.
        spawn_defaults = self._session_starter_defaults()
        spawn_defaults['permission_mode'] = 'plan'
        try:
            self._session_manager.start_session(
                task_id=str(task.id),
                task_summary=str(task.summary or ''),
                # ``claude -p --input-format stream-json`` stays alive
                # across multiple user messages, but it must receive at
                # least one prompt at startup — empty stdin causes it
                # to exit with an error and the scan loop would respawn
                # it forever. The contextual prompt below puts Claude
                # in "ready, waiting" state without kicking off any work.
                initial_prompt=self._build_wait_planning_prompt(task),
                cwd=cwd,
                expected_branch=expected_branch,
                **spawn_defaults,
            )
            self.logger.info(
                'task %s tagged %s — registered planning chat (cwd=%s); '
                'remove the tag to let the agent run autonomously',
                task.id,
                TaskTags.WAIT_PLANNING,
                cwd or '?',
            )
        except Exception:
            self.logger.exception(
                'failed to register planning session for task %s', task.id
            )
        # Planning is real work — move the ticket out of the inbox so it
        # doesn't get picked up by another agent / scanned again as
        # "needs to start". Idempotent on the ticket side, and only
        # called on the fresh-spawn branch (the early "alive" return
        # above guards the steady state).
        self._move_wait_planning_task_to_in_progress(task)
        return skip_task_result(task.id, [])

    def _move_wait_planning_task_to_in_progress(self, task: Task) -> None:
        """Move the ticket to "In Progress" when its planning session opens.

        Best-effort: failures (network, permission, already-in-state)
        are logged but never block the chat from working. The autonomous
        flow uses ``_start_task_processing`` for the same step; we keep
        them separate because wait-planning skips the rest of that
        helper's work (commenting, branch push validation, etc).
        """
        try:
            self._task_state_service.move_task_to_in_progress(task.id)
            self.logger.info(
                'task %s moved to in progress for planning session', task.id,
            )
        except Exception:
            self.logger.exception(
                'failed to move planning task %s to in progress', task.id,
            )

    def _resolve_wait_planning_context(self, task: Task) -> tuple[str, str]:
        """Resolve and prepare the task branch for a wait-planning session.

        Returns ``(cwd, expected_branch)``. We deliberately DO check out
        the task branch (e.g. ``UNA-2576``) before the chat opens —
        otherwise Claude would edit master if the user asks it to make
        changes during planning. Branch creation reuses the autonomous
        path's flow (fetch origin → fast-forward master → cut the task
        branch from origin/master), just done up-front so the chat
        starts on a safe base.

        Best-effort: any failure (no repo match, git fetch error, etc.)
        falls back to a more conservative result so the chat tab still
        opens — the user sees an empty Files / Changes pane and can
        investigate, but the conversation isn't blocked.
        """
        repositories = self._wait_planning_resolved_repositories(task)
        if not repositories:
            return '', ''
        repositories = self._wait_planning_prepared_repositories(task, repositories)
        if not repositories:
            return '', ''
        primary = repositories[0]
        cwd = str(getattr(primary, 'local_path', '') or '').strip()
        branch_name = self._wait_planning_branch_name(task, primary)
        if not branch_name:
            return cwd, ''
        if not self._wait_planning_check_out_branches(task, repositories, primary, branch_name):
            return cwd, ''
        return cwd, branch_name

    def _wait_planning_resolved_repositories(self, task: Task) -> list:
        try:
            return list(
                self._repository_service.resolve_task_repositories(task) or [],
            )
        except Exception:
            self.logger.exception(
                'failed to resolve repositories for wait-planning task %s', task.id,
            )
            return []

    def _wait_planning_prepared_repositories(
        self,
        task: Task,
        repositories: list,
    ) -> list:
        try:
            return list(
                self._repository_service.prepare_task_repositories(repositories) or [],
            )
        except Exception:
            self.logger.exception(
                'failed to prepare repositories for wait-planning task %s', task.id,
            )
            # Caller falls back to ``cwd=local_path, expected_branch=''``
            # — return [] so the caller takes that path uniformly.
            return []

    def _wait_planning_branch_name(self, task: Task, primary_repository) -> str:
        try:
            return str(
                self._repository_service.build_branch_name(task, primary_repository) or '',
            ).strip()
        except Exception:
            self.logger.exception(
                'failed to derive branch name for wait-planning task %s', task.id,
            )
            return ''

    def _wait_planning_check_out_branches(
        self,
        task: Task,
        repositories: list,
        primary_repository,
        branch_name: str,
    ) -> bool:
        """Check out the task branch on the primary + sibling repositories.

        Returns True on success, False on git failure. Logs siblings that
        got pulled in so the operator can see the scope of the change.
        """
        with_siblings = self._repositories_with_siblings(repositories, primary_repository)
        repository_branches = {repo.id: branch_name for repo in with_siblings}
        try:
            self._repository_service.prepare_task_branches(
                with_siblings, repository_branches,
            )
        except Exception:
            self.logger.exception(
                'failed to check out task branch for wait-planning task %s; '
                'chat will open on whatever branch is currently checked out',
                task.id,
            )
            return False
        sibling_ids = [
            repo.id for repo in with_siblings if repo not in repositories
        ]
        if sibling_ids:
            self.logger.info(
                'task %s: also synced sibling repos %s to branch %s',
                task.id, sibling_ids, branch_name,
            )
        return True

    def _repositories_with_siblings(
        self,
        primary_repositories: list,
        anchor_repository,
    ) -> list:
        """Append sibling repos (sharing a parent dir) to the prep list.

        Deduplicates by ``id`` against ``primary_repositories`` so an
        explicitly tagged repo isn't prepped twice.
        """
        sibling_lookup = getattr(
            self._repository_service, 'sibling_repositories', None,
        )
        if sibling_lookup is None:
            return list(primary_repositories)
        try:
            siblings = sibling_lookup(anchor_repository) or []
        except Exception:
            self.logger.exception(
                'failed to look up sibling repositories for %s',
                getattr(anchor_repository, 'id', '?'),
            )
            return list(primary_repositories)
        seen_ids = {getattr(r, 'id', '') for r in primary_repositories}
        novel_siblings = [
            sibling for sibling in siblings
            if getattr(sibling, 'id', '') and getattr(sibling, 'id', '') not in seen_ids
        ]
        return list(primary_repositories) + novel_siblings

    @staticmethod
    def _build_wait_planning_prompt(task: Task) -> str:
        """Initial prompt for a wait-planning chat tab.

        Three jobs at once:
          1. Hand Claude the full task description so it has context.
          2. Hard-stop any tool use — wait-planning is **planning only**.
             We have to be explicit because the agent's default behavior
             when handed a task is to start working on it.
          3. Avoid empty stdin (which makes ``claude -p`` exit with an
             error and the scan loop would respawn it forever).
        """
        task_id = str(getattr(task, 'id', '') or '').strip()
        summary = str(getattr(task, 'summary', '') or '').strip()
        description = str(getattr(task, 'description', '') or '').strip()
        header = f'YouTrack ticket {task_id}' if task_id else 'this task'

        sections = [
            f"You're pair-planning with the user on {header}.",
            '',
            '## Task summary',
            summary or '(no summary provided)',
        ]
        if description:
            sections.extend(['', '## Task description', description])
        sections.extend([
            '',
            '## Operating rules — READ CAREFULLY',
            '- This is a **planning-only** session. DO NOT call any tools.',
            '- DO NOT read, edit, write, or run anything.',
            '- DO NOT touch the filesystem, the shell, or the network.',
            '- Your job is to discuss the task with the user, ask clarifying '
            'questions, and help them refine the approach in plain text.',
            '- The user will explicitly tell you when planning is done. Until '
            'then, every reply is a discussion message — no tool calls.',
            '',
            'Briefly acknowledge that you understand and are ready to plan. '
            'Then wait for the user to drive the conversation.',
        ])
        return '\n'.join(sections)

    # Fields the streaming runner exposes that ``start_session`` accepts.
    # Strings get an empty-string fallback (avoid ``None`` slipping through
    # to subprocess args); ``max_turns`` is passed through verbatim because
    # ``None`` is the legitimate "no cap" sentinel.
    _SESSION_STRING_FIELDS = (
        'binary',
        'model',
        'permission_mode',
        'permission_prompt_tool',
        'allowed_tools',
        'disallowed_tools',
        'effort',
    )

    def _session_starter_defaults(self) -> dict[str, object]:
        """Forward the runner's defaults to start_session(...).

        Used by the wait-planning short-circuit to spawn a chat tab with
        the same binary / model / permission settings the autonomous
        path would use.
        """
        runner = self._planning_session_runner
        if runner is None:
            return {}
        defaults = getattr(runner, '_defaults', None)
        if defaults is None:
            return {}
        result: dict[str, object] = {
            field: (getattr(defaults, field, '') or '')
            for field in self._SESSION_STRING_FIELDS
        }
        result['max_turns'] = getattr(defaults, 'max_turns', None)
        return result

    @staticmethod
    def _task_has_wait_planning_tag(task: Task) -> bool:
        tags = getattr(task, 'tags', None) or []
        target = TaskTags.WAIT_PLANNING.lower()
        for tag in tags:
            if str(tag or '').strip().lower() == target:
                return True
        return False

    def _start_task_processing(self, task: Task, prepared_task: PreparedTaskContext) -> bool:
        try:
            self._log_task_step(task.id, 'moving issue to in progress')
            self._task_state_service.move_task_to_in_progress(task.id)
            self._log_task_step(task.id, 'moved issue to in progress')
        except Exception as exc:
            self._task_failure_handler.handle_task_failure(task, exc, prepared_task=prepared_task)
            return False
        self._task_publisher.comment_task_started(task, prepared_task.repositories)
        return True

    def _run_task_implementation(
        self,
        task: Task,
        prepared_task: PreparedTaskContext,
    ) -> dict[str, str | bool] | None:
        self._log_task_step(task.id, 'starting implementation')
        # ``kato:wait-planning`` is short-circuited earlier — by the time we
        # get here the task is one we *will* execute. Route through the
        # streaming runner when it's wired so the user can watch the work
        # (and intercept permission prompts) in the planning UI. Permission
        # modes are baked into the runner's defaults at construction time.
        runner = self._planning_session_runner
        try:
            if runner is not None:
                self._log_task_step(
                    task.id,
                    'streaming planning session (kato:wait-planning + bypass=false)',
                )
                execution = runner.implement_task(task, prepared_task=prepared_task) or {}
            else:
                execution = self._implementation_service.implement_task(
                    task,
                    prepared_task=prepared_task,
                ) or {}
        except Exception as exc:
            self.logger.exception('implementation request failed for task %s', task.id)
            self._task_failure_handler.handle_started_task_failure(
                task,
                exc,
                prepared_task=prepared_task,
            )
            return None
        if not implementation_succeeded(execution):
            self._task_failure_handler.handle_implementation_failure(
                task,
                execution,
                prepared_task=prepared_task,
            )
            return None
        self._log_task_step(
            task.id,
            'implementation completed successfully%s',
            session_suffix(execution),
        )
        return execution

    def _run_task_testing_validation(
        self,
        task: Task,
        prepared_task: PreparedTaskContext,
        execution: dict[str, str | bool],
    ) -> tuple[bool, dict | None, dict[str, str | bool]]:
        if self._skip_testing:
            execution = dict(execution)
            execution.pop(ImplementationFields.MESSAGE, None)
            self._log_task_step(task.id, 'testing validation skipped by configuration')
            return True, None, execution
        if not self._task_preflight_service.validate_task_branch_publishability(
            task,
            prepared_task,
            failure_handler=self._task_failure_handler.handle_started_task_failure,
        ):
            return False, None, execution
        self._log_task_step(task.id, 'task branches contain changes')
        testing = self._request_testing_validation(task, prepared_task)
        if testing is None:
            return False, None, execution
        if not testing_succeeded(testing):
            self._task_failure_handler.handle_testing_failure(
                task,
                testing,
                prepared_task=prepared_task,
            )
            return False, testing_failed_result(task.id), execution
        execution = apply_testing_message(execution, testing)
        self._log_task_step(task.id, 'testing validation passed')
        return True, None, execution

    def _request_testing_validation(
        self,
        task: Task,
        prepared_task: PreparedTaskContext,
    ) -> dict[str, str | bool] | None:
        self._log_task_step(task.id, 'starting testing validation')
        try:
            return self._testing_service.test_task(
                task,
                prepared_task=prepared_task,
            ) or {}
        except Exception as exc:
            self.logger.exception('testing request failed for task %s', task.id)
            self._task_failure_handler.handle_started_task_failure(
                task,
                exc,
                prepared_task=prepared_task,
            )
            return None

    def _log_task_step(self, task_id: str, message: str, *args) -> None:
        log_mission_step(self.logger, task_id, message, *args)
