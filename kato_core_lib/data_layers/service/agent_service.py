from __future__ import annotations

import logging
import threading

from core_lib.data_layers.service.service import Service

from kato_core_lib.data_layers.service.agent_state_registry import AgentStateRegistry
from kato_core_lib.data_layers.service.task_failure_handler import TaskFailureHandler
from kato_core_lib.data_layers.service.review_comment_service import ReviewCommentService
from kato_core_lib.data_layers.service.task_publisher import TaskPublisher
from kato_core_lib.data_layers.service.task_state_service import TaskStateService
from kato_core_lib.validation.repository_connections import (
    RepositoryConnectionsValidator,
)
from kato_core_lib.validation.startup_dependency_validator import (
    StartupDependencyValidator,
)
from kato_core_lib.helpers.late_binding import call_later, later
from kato_core_lib.helpers.logging_utils import configure_logger
from kato_core_lib.helpers.mission_logging_utils import MissionStepLoggerMixin
from kato_core_lib.data_layers.data.task import Task
from kato_core_lib.data_layers.service.implementation_service import ImplementationService
from kato_core_lib.helpers.push_approval_gate_utils import (
    AUTO_PUSH_DISABLED_REASON,
    AUTO_PUSH_ENABLED_KEY,
    auto_push_enabled,
)
from kato_core_lib.helpers.task_context_utils import PreparedTaskContext, session_suffix
from kato_core_lib.helpers.task_lookup_utils import find_assigned_or_review_task
from kato_core_lib.data_layers.service.notification_service import NotificationService
from kato_core_lib.data_layers.service.repository_service import RepositoryService
from kato_core_lib.data_layers.service.planning_session_runner import (
    SessionStoppedByUserError,
)
from kato_core_lib.data_layers.service.task_preflight_service import (
    TaskPreflightService,
)
from kato_core_lib.data_layers.service.task_service import TaskService
from kato_core_lib.data_layers.service.testing_service import TestingService
from kato_core_lib.data_layers.service.workspace_manager import (
    WORKSPACE_STATUS_ERRORED,
    WORKSPACE_STATUS_REVIEW,
)
from kato_core_lib.data_layers.data.fields import (
    ImplementationFields,
    StatusFields,
    TaskTags,
)
from kato_core_lib.validation.branch_publishability import (
    TaskBranchPublishabilityValidator,
)
from kato_core_lib.validation.branch_push import TaskBranchPushValidator
from kato_core_lib.validation.model_access import TaskModelAccessValidator
from kato_core_lib.helpers.task_execution_utils import (
    apply_testing_message,
    implementation_succeeded,
    testing_failed_result,
    testing_succeeded,
)
# ``RepositoryHasNoChangesError`` is the "no work to publish" outcome
# from the publish path. With the per-repo ``branch_needs_push``
# pre-filter in ``push_task`` we shouldn't trip it normally, but a
# concurrent push or a workspace state change can still race us — log
# those one-liners and move on instead of dumping a full stack trace.

# How long a sent-but-unanswered user message may sit before the
# session is judged STALLED (alive, but no longer consuming stdin).
# Must comfortably exceed the normal "message sent, Claude warming
# up" window: a healthy turn flips ``is_working`` True (events
# flowing) or returns a ``result`` well inside this window, so only a
# subprocess that silently stopped reading stdin stays
# ``user_messages_sent > result_events_received`` past it. Read by
# ``_task_session_is_stalled``; the classic trigger is a post-restart
# ``--resume`` respawn that never picks up the piped message.
#
# This is the SAME budget the Claude transport's ``is_working`` uses to keep
# an unacked turn reading "working" during warm-up — imported as one value so
# the two can't drift: ``_task_session_is_stalled`` requires ``is_working`` to
# have already flipped False before it ages a stall out.
def _require_collaborators(**collaborators) -> None:
    """Fail fast when a required collaborator is missing, naming which one.

    Wiring mistakes surface at construction with the parameter's name rather
    than as an ``AttributeError`` on ``None`` somewhere deep in a scan tick.
    """
    for name, value in collaborators.items():
        if value is None:
            raise ValueError(f'{name} is required')


def _agreed_state_registry(state_registry, review_comment_service):
    """The one registry the agent and the review-comment service both use.

    Two registries would mean two views of what is running: the review path
    would mark a task busy where the scan loop cannot see it. Passing a
    registry that disagrees with the review service's is a wiring bug, so it
    raises rather than silently picking one.
    """
    if review_comment_service is None:
        return state_registry
    review_state_registry = review_comment_service.state_registry
    if state_registry is not None and review_state_registry is not state_registry:
        raise ValueError(
            'state_registry must match review_comment_service.state_registry'
        )
    return state_registry or review_state_registry


class AgentService(MissionStepLoggerMixin, Service):
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
        workspace_manager=None,
        parallel_task_runner=None,
        wait_planning_service=None,
        triage_service=None,
        review_workspace_ttl_seconds: float = 3600.0,
        lessons_service=None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.logger = logger or configure_logger(self.__class__.__name__)
        _require_collaborators(
            task_service=task_service,
            task_state_service=task_state_service,
            implementation_service=implementation_service,
            testing_service=testing_service,
            repository_service=repository_service,
            notification_service=notification_service,
        )
        state_registry = _agreed_state_registry(state_registry, review_comment_service)
        self._task_service = task_service
        self._task_state_service = task_state_service
        self._implementation_service = implementation_service
        self._testing_service = testing_service
        self._repository_service = repository_service
        self._notification_service = notification_service
        self._skip_testing = bool(skip_testing)
        self._planning_session_runner = planning_session_runner
        self._session_manager = session_manager
        self._workspace_manager = workspace_manager
        self._parallel_task_runner = parallel_task_runner
        self._wait_planning_service = wait_planning_service
        self._triage_service = triage_service
        self._review_workspace_ttl_seconds = max(
            0.0, float(review_workspace_ttl_seconds or 0.0),
        )
        self._lessons_service = lessons_service
        # `kato:wait-before-git-push` plumbing. The dict stashes the
        # (task, prepared_task, execution) tuple after testing so the
        # operator-triggered ``approve_push`` can resume publish without
        # re-running the agent. In-memory only — a kato restart loses
        # pending approvals (the workspace branch + commits survive on
        # disk; operator can re-trigger by removing the tag and letting
        # the next scan re-process the task).
        # ``RLock`` rather than ``Lock`` so a future approve-flow callback
        # that re-enters ``is_awaiting_push_approval`` from inside another
        # critical section won't deadlock.
        self._pending_publish_lock = threading.RLock()
        self._pending_publish: dict[str, tuple] = {}
        self._build_default_collaborators(
            state_registry=state_registry,
            review_comment_service=review_comment_service,
            repository_connections_validator=repository_connections_validator,
            task_failure_handler=task_failure_handler,
            startup_validator=startup_validator,
            task_preflight_service=task_preflight_service,
            task_publisher=task_publisher,
        )
        self._build_subsystems()

    def _build_default_collaborators(
        self,
        *,
        state_registry,
        review_comment_service,
        repository_connections_validator,
        task_failure_handler,
        startup_validator,
        task_preflight_service,
        task_publisher,
    ) -> None:
        """Accept the collaborators the caller supplied; build the rest.

        Every one of these is injectable (tests and the setup-mode boot pass
        their own) and every one has an obvious default assembled from the
        services already stored. Kept out of ``__init__`` so the constructor
        reads as "check, store, build" rather than as an assembly line.
        """
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

    def _build_subsystems(self) -> None:
        """Build the sub-services this object is a facade over.

        Order matters: the lesson service is a collaborator of the comment and
        publish subsystems, and the repositories subsystem reconciles a task's
        repos before publish reads their metadata.
        """
        # Lesson capture (chat prompts, comments, finished tasks). Built
        # first: the comment and publish subsystems take it as a collaborator.
        from kato_core_lib.data_layers.service.task_lesson_service import (
            TaskLessonService,
        )
        self._task_lesson_service = TaskLessonService(
            lessons_service=later(self, '_lessons_service'),
            logger=later(self, 'logger'),
        )

        # Cleaning up after finished tasks: workspaces, planning sessions,
        # conversations. Built before the comment subsystem, which releases a
        # done task's conversation through it.
        from kato_core_lib.data_layers.service.task_cleanup_service import (
            TaskCleanupService,
        )
        self._task_cleanup_service = TaskCleanupService(
            task_service=later(self, '_task_service'),
            session_manager=later(self, '_session_manager'),
            workspace_manager=later(self, '_workspace_manager'),
            implementation_service=later(self, '_implementation_service'),
            state_registry=later(self, '_state_registry'),
            review_workspace_ttl_seconds=later(self, '_review_workspace_ttl_seconds'),
            logger=later(self, 'logger'),
        )

        # Running an agent for a comment (queue → dispatch → completion) is
        # its own subsystem. It and the comment records are mutual
        # collaborators — the records trigger a run, a finished run marks a
        # record addressed — so each resolves the other lazily.
        from kato_core_lib.data_layers.service.task_comment_run_service import (
            TaskCommentRunService,
        )
        self._task_comment_run_service = TaskCommentRunService(
            comment_service=later(self, '_task_comment_service'),
            session_manager=later(self, '_session_manager'),
            workspace_manager=later(self, '_workspace_manager'),
            parallel_task_runner=later(self, '_parallel_task_runner'),
            planning_session_runner=later(self, '_planning_session_runner'),
            cleanup_service=self._task_cleanup_service,
            logger=later(self, 'logger'),
        )

        # Comments (operator diff comments + provider PR review comments) are
        # their own subsystem: one store, one queue, one scheduler. They were
        # 42% of this class; the logic lives in TaskCommentService now and
        # this object forwards to it.
        from kato_core_lib.data_layers.service.task_comment_service import (
            TaskCommentService,
        )
        # ``later``/``call_later`` resolve off this object at CALL time, so a
        # collaborator replaced afterwards (or patched by a test) is seen by
        # the sub-service — passing the objects directly would freeze whatever
        # existed at construction.
        self._task_comment_service = TaskCommentService(
            workspace_manager=later(self, '_workspace_manager'),
            session_manager=later(self, '_session_manager'),
            review_comment_service=later(self, '_review_comment_service'),
            repository_service=later(self, '_repository_service'),
            parallel_task_runner=later(self, '_parallel_task_runner'),
            planning_session_runner=later(self, '_planning_session_runner'),
            lesson_service=self._task_lesson_service,
            cleanup_service=self._task_cleanup_service,
            run_service=self._task_comment_run_service,
            logger=later(self, 'logger'),
        )

        # The repositories one task touches: the ticket's kato:repo: tags,
        # the workspace metadata, and the clones on disk. Built before the
        # publish service, which reconciles them before it reads metadata.
        from kato_core_lib.data_layers.service.task_repository_service import (
            TaskRepositoryService,
        )
        self._task_repository_service = TaskRepositoryService(
            repository_service=later(self, '_repository_service'),
            task_service=later(self, '_task_service'),
            workspace_manager=later(self, '_workspace_manager'),
            session_manager=later(self, '_session_manager'),
            logger=later(self, 'logger'),
        )

        # Publishing (push / pull / merge / PR / update-source) and the
        # push-approval hold: the second subsystem to get its own object.
        from kato_core_lib.data_layers.service.task_publish_service import (
            TaskPublishService,
        )
        self._task_publish_service = TaskPublishService(
            repository_service=later(self, '_repository_service'),
            task_service=later(self, '_task_service'),
            task_state_service=later(self, '_task_state_service'),
            task_publisher=later(self, '_task_publisher'),
            workspace_manager=later(self, '_workspace_manager'),
            lesson_service=self._task_lesson_service,
            # SHARED state, not a copy: the autonomous flow parks a finished
            # task in this dict and the UI's approve button resumes it.
            pending_publish=self._pending_publish,
            pending_publish_lock=self._pending_publish_lock,
            update_workspace_status_after_publish=call_later(
                self, '_update_workspace_status_after_publish',
            ),
            reconcile_task_repositories=call_later(
                self._task_repository_service, 'reconcile_task_repositories',
            ),
            logger=later(self, 'logger'),
        )

    @property
    def comment_runs(self):
        """The comment run engine — ``agent_service.comment_runs.advance_finished_comment_runs()``.

        Queue → dispatch → completion for operator comments. The records
        themselves live on ``comments``. See TaskCommentRunService.
        """
        return self._task_comment_run_service

    @property
    def cleanup(self):
        """The cleanup subsystem — ``agent_service.cleanup.cleanup_done_tasks()``.

        Releasing what finished tasks left behind, without deleting anything.
        See TaskCleanupService.
        """
        return self._task_cleanup_service

    @property
    def lessons(self):
        """The lessons subsystem — ``agent_service.lessons.promote_candidates(…)``.

        Staging candidates, promoting them, and mining a finished task. Every
        entry point is best-effort and runs in the background. See
        TaskLessonService.
        """
        return self._task_lesson_service

    @property
    def repositories(self):
        """The repositories subsystem — ``agent_service.repositories.sync_task_repositories(…)``.

        What repositories a task touches, and keeping the ticket tags, the
        workspace metadata, and the clones on disk agreeing. See
        TaskRepositoryService.
        """
        return self._task_repository_service

    @property
    def publish(self):
        """The publish subsystem — ``agent_service.publish.push_task(…)``.

        Push / pull / merge / pull requests / source sync, plus the
        push-approval hold. See TaskPublishService.
        """
        return self._task_publish_service

    @property
    def comments(self):
        """The task-comment subsystem — ``agent_service.comments.add_task_comment(…)``.

        Namespaced rather than forwarded: 24 one-line pass-throughs on this
        class hid where the logic lived, and made a patched method on this
        object silently ignored by the sub-service's own internal calls.
        """
        return self._task_comment_service

    @property
    def notification_service(self) -> NotificationService:
        return self._notification_service

    def validate_connections(self) -> None:
        self._startup_validator.validate(self.logger)

    def warm_up_repository_inventory(self) -> None:
        """Trigger repository auto-discovery in the background.

        Without this, the disk walk that finds all .git folders under
        REPOSITORY_ROOT_PATH fires lazily on the *first task pickup*,
        blocking that task for however long the walk takes. Calling
        this right after startup means the walk runs in parallel with
        the first scan-interval sleep, so first-task latency is zero
        instead of "however large the project tree is".

        Errors are swallowed — the walk will re-run on first task pickup
        as before, so a transient failure here is non-fatal.
        """
        import threading
        repo_service = self._repository_service

        def _run() -> None:
            try:
                repo_service._ensure_repositories()
            except Exception:
                pass

        t = threading.Thread(target=_run, daemon=True, name='kato-repo-inventory-warmup')
        t.start()

    def shutdown(self) -> None:
        """Tear down everything kato owns: pool, sessions, conversations.

        Wired into the kato main process's signal handler. Each step is
        guarded so a single failure can't block the rest of the cleanup.
        Idempotent — safe to call twice.
        """
        if self._parallel_task_runner is not None:
            try:
                self._parallel_task_runner.shutdown(wait=True)
            except Exception:
                self.logger.exception('error during parallel-runner shutdown')
        try:
            self._implementation_service.stop_all_conversations()
        except Exception:
            self.logger.exception('error stopping implementation conversations')
        try:
            self._testing_service.stop_all_conversations()
        except Exception:
            self.logger.exception('error stopping testing conversations')
        if self._session_manager is not None:
            try:
                self._session_manager.shutdown()
            except Exception:
                self.logger.exception('error tearing down planning sessions on shutdown')

    def get_assigned_tasks(self) -> list[Task]:
        return self._task_service.get_assigned_tasks()


    @property
    def parallel_task_runner(self):
        """Worker pool used by the scan job to run tasks concurrently.

        ``None`` when the runner wasn't wired (legacy / test setups).
        Callers are expected to fall back to inline execution in that
        case so the same code path works with and without the runner.
        """
        return self._parallel_task_runner









    def mark_task_done(self, task_id: str) -> None:
        """Move ``task_id``'s ticket to the tracker's done column.

        Backs the "this task is done" checkbox on the forget dialog:
        the operator is deleting the local clone AND declaring the work
        finished, so the ticket moves to done (``<platform>_DONE_STATE``,
        e.g. YouTrack/Jira ``Done``, GitHub/GitLab ``closed``) instead of
        being left behind in whatever column kato last put it in.

        Raises on failure — the caller surfaces the error and (in the
        DELETE endpoint) refuses to wipe anything, so an operator never
        gets told "moved to done" when the ticket didn't move.
        """
        normalized = str(task_id or '').strip()
        if not normalized:
            raise ValueError('task id is required')
        self._task_state_service.move_task_to_done(normalized)


    def forget_task_state(self, task_id: str) -> None:
        """Drop the registry state for a task the operator deleted.

        Removes the task's PR contexts / task-map entries AND its PERSISTED
        processed-review-comment marks, so deleting a task also cleans
        ~/.kato/processed_review_comments.json instead of leaving marks for a
        task that no longer exists. Called by the DELETE workspace endpoint;
        the autonomous done-task sweep already calls the same registry hook.
        """
        self._state_registry.forget_task(task_id)








        # Intentionally a no-op — see ``_mark_workspace_done_silent``.

    def _update_workspace_status_after_publish(
        self,
        task_id: str,
        publish_result: dict[str, object] | None,
    ) -> None:
        if self._workspace_manager is None or not publish_result:
            return
        status = publish_result.get(StatusFields.STATUS)
        if status == StatusFields.READY_FOR_REVIEW:
            target = WORKSPACE_STATUS_REVIEW
        elif status == StatusFields.PARTIAL_FAILURE:
            target = WORKSPACE_STATUS_ERRORED
        else:
            return
        try:
            self._workspace_manager.update_status(str(task_id), target)
        except Exception:
            self.logger.exception(
                'failed to update workspace status for task %s to %s',
                task_id, target,
            )









    def process_assigned_task(self, task: Task) -> dict[str, object] | None:
        # The ticket's ``kato:repo:`` tags are the durable statement of
        # which repos this task touches, and this is where kato SEES them
        # change. Fold any newly-tagged repo into the workspace metadata
        # first — for a live chat task the scan stops at the short-circuit
        # below, so without this the tag would never reach
        # ``.kato-meta.json`` and the repo would be edited but never
        # pushed. The task object is already in hand, so no extra
        # ticket-platform call; a task with no workspace is a no-op.
        self.repositories.reconcile_task_repositories(str(task.id), task=task)

        # No in-memory "already processed" short-circuit. The ticket system
        # (state + comments) is the single source of truth: successful tasks
        # have already been moved out of the scanned states, and skipped/
        # failed tasks carry comments that the gate and preflight read fresh
        # on every scan. Remove the comment, the task is re-evaluated.

        # `kato:triage:investigate` short-circuits the orchestration too,
        # but for a different reason: instead of registering an
        # interactive chat, kato spends one Claude turn classifying the
        # task and writes back a kato:triage:<level> outcome tag. No
        # implementation, no testing, no PR. Runs *before* wait-planning
        # so a triage task that also carries the wait-planning tag
        # still gets classified rather than opened as a chat tab.
        if self._triage_service is not None:
            triage_result = self._triage_service.handle_task(task)
            if triage_result is not None:
                return triage_result

        # `kato:wait-planning` short-circuits the orchestration: register the
        # planning tab so the human can chat with the agent in the UI, but
        # do *no* implementation, testing, or publishing work. The user
        # controls the conversation; remove the tag whenever they want
        # autonomous execution to take over.
        if self._wait_planning_service is not None:
            planning_only_result = self._wait_planning_service.handle_task(task)
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
        if self._should_pause_for_push_approval(task):
            return self._pause_for_push_approval(task, prepared_task, execution)
        return self.publish.publish_execution(task, prepared_task, execution)

    @staticmethod
    def _should_pause_for_push_approval(task: Task) -> bool:
        """Must this finished task wait for the operator before publishing?

        Publishing is an operator action, so the default is yes. Autonomous
        push + PR happens only when ``KATO_AUTO_PUSH_ENABLED`` is explicitly
        on, and even then the per-task ``kato:wait-before-git-push`` tag still
        forces a pause — the tag is a stricter statement than the global
        switch and must not be overridden by it.
        """
        if not auto_push_enabled():
            return True
        return AgentService._task_has_wait_before_push_tag(task)

    @staticmethod
    def _task_has_wait_before_push_tag(task: Task) -> bool:
        tags = getattr(task, 'tags', None) or []
        target = TaskTags.WAIT_BEFORE_GIT_PUSH.lower()
        for tag in tags:
            if str(tag or '').strip().lower() == target:
                return True
        return False

    def _pause_for_push_approval(
        self,
        task: Task,
        prepared_task: PreparedTaskContext,
        execution: dict,
    ) -> dict[str, object]:
        """Stash the post-test execution context and post a "waiting" comment.

        The actual push happens via :meth:`approve_push` (called from the
        planning UI's "Approve push" button). We do NOT post the
        ``Kato completed task`` blocking-comment prefix here — that one
        signals success and would prevent re-processing. Instead we use
        a one-off informational comment that does not interfere with the
        existing comment-driven blocker mechanism.
        """
        task_id = str(task.id)
        with self._pending_publish_lock:
            self._pending_publish[task_id] = (task, prepared_task, execution)
        # Name the reason that actually applies. The tag used to be the only
        # way to get here, so the comment hard-coded it; now the default is a
        # pause and most parked tasks carry no tag at all.
        if self._task_has_wait_before_push_tag(task):
            reason = f'`{TaskTags.WAIT_BEFORE_GIT_PUSH}` is set'
            remedy = (
                'click "Approve push" in the planning UI, or remove the '
                f'`{TaskTags.WAIT_BEFORE_GIT_PUSH}` tag and re-trigger the '
                'task'
            )
        else:
            reason = AUTO_PUSH_DISABLED_REASON
            remedy = (
                'click "Approve push" in the planning UI, or set '
                f'`{AUTO_PUSH_ENABLED_KEY}` to publish without asking'
            )
        try:
            self._task_service.add_comment(
                task_id,
                'Kato has finished implementation and testing for this task. '
                f'Push and PR creation are paused because {reason}. '
                f'To proceed, {remedy}. '
                'Kato — not the agent — performs the push.',
            )
        except Exception:
            self.logger.exception(
                'failed to post wait-before-push comment for task %s', task_id,
            )
        if self._workspace_manager is not None:
            try:
                self._workspace_manager.update_status(
                    task_id, WORKSPACE_STATUS_REVIEW,
                )
            except Exception:
                self.logger.exception(
                    'failed to update workspace status for task %s', task_id,
                )
        self.logger.info(
            'task %s implementation complete; awaiting push approval', task_id,
        )
        return {
            StatusFields.STATUS: 'awaiting_push_approval',
            'task_id': task_id,
        }



    # ----- diff-tab comments (kato-local + remote-synced) -----










    # Continuation / acknowledgement prompts carry no generalizable lesson.
    # Mining them just fires a throwaway ``claude -p`` per low-signal message
    # — the WorkingIndicator "continue" button, the Resume "please continue
    # from where you left off", "ok", "yes", etc. — which spammed the
    # operator's Claude session history. Skip them so candidate extraction
    # only runs on prompts that could actually encode a rule. Normalized
    # (lowercased, whitespace-collapsed, trailing punctuation stripped).































































    # ----- on-demand push / PR (planning UI buttons) -----



    def list_all_assigned_tasks(self) -> list[dict[str, str]]:
        """Return ``{id, summary, state, description}`` for every task assigned.

        Drives the planning UI's "+ Add task" picker. Spans the full
        ticket lifecycle (open / in progress / in review / done) so
        the operator can drop any of their tickets into kato — even
        completed ones for retrospective review or to re-run an
        agent against the existing branch.
        """
        try:
            tasks = self._task_service.list_all_assigned_tasks()
        except Exception:
            self.logger.exception('failed to list all assigned tasks')
            return []
        out: list[dict[str, str]] = []
        for task in tasks or []:
            out.append({
                'id': str(getattr(task, 'id', '') or ''),
                'summary': str(getattr(task, 'summary', '') or ''),
                'state': str(getattr(task, 'state', '') or ''),
                'description': str(getattr(task, 'description', '') or '')[:500],
            })
        return out

    def adopt_task(self, task_id: str) -> dict[str, object]:
        """Provision a workspace + clones for a task the operator picked.

        Drives the "+ Add task" flow on the left panel. Mirrors the
        autonomous initial-task path's first three steps (resolve
        repos → REP gate → workspace clones) so an operator-picked
        task has the same on-disk shape as one kato discovered via
        the queue scan. Skips the agent spawn — the operator will
        type into the chat tab when they're ready.

        Idempotent on already-adopted: if the workspace already
        exists, ``provision_task_workspace_clones`` reuses the
        existing clones (the create call is a no-op for an existing
        record), so re-clicking a task in the picker doesn't blow
        anything away.
        """
        normalized = str(task_id or '').strip()
        if not normalized:
            return {'adopted': False, 'error': 'empty task id'}
        # Re-adopting a forgotten task re-engages it: clear the persistent
        # "forgotten" mark so the review-comment scan polls its PR again.
        from kato_core_lib.helpers.forgotten_tasks_store import unforget
        unforget(normalized)
        if self._workspace_manager is None:
            return {
                'adopted': False, 'task_id': normalized,
                'error': 'workspace manager not wired',
            }
        # Find the live Task — needed for tags/description-driven
        # repo resolution.
        task_obj = find_assigned_or_review_task(self._task_service, normalized)
        if task_obj is None:
            return {
                'adopted': False, 'task_id': normalized,
                'error': (
                    f'task {normalized!r} is not assigned to this kato '
                    f'(or the ticket platform refused the lookup)'
                ),
            }
        # Resolve all task repos via the same path the autonomous
        # flow uses; refuse the adoption when REP says no.
        try:
            repositories = self._repository_service.resolve_task_repositories(
                task_obj,
            )
        except Exception as exc:
            return {
                'adopted': False, 'task_id': normalized,
                'error': f'failed to resolve task repositories: {exc}',
            }
        # REP gate. We don't have the failure-handler chain that
        # the autonomous path uses (it posts a ticket comment),
        # so we surface as a structured error and let the UI
        # render the "approve repo first" message.
        try:
            from kato_core_lib.data_layers.service.repository_approval_service import (
                RepositoryApprovalService,
            )
            # Same helper the autonomous preflight uses, so the operator's
            # "+ Add task" and the scan loop can never disagree about which
            # repositories REP refuses.
            unapproved = RepositoryApprovalService().unapproved_repository_ids(
                repositories,
            )
            if unapproved:
                return {
                    'adopted': False, 'task_id': normalized,
                    'error': (
                        f'restricted execution protocol: refusing — '
                        f'no approval on record for repository id(s) '
                        f'{", ".join(unapproved)}. Run '
                        f'``kato approve-repo`` and retry.'
                    ),
                    'unapproved_repositories': unapproved,
                }
        except Exception:
            # Approval service blew up — log and proceed; the
            # autonomous path's REP enforcement will catch it on
            # the next scan if there's a real problem.
            self.logger.exception(
                'REP approval check crashed for adopt_task on %s; '
                'skipping the gate',
                normalized,
            )
        # Provision clones via the same workspace_provisioner the
        # autonomous flow uses.
        from kato_core_lib.data_layers.service.workspace_provisioning_service import (
            provision_task_workspace_clones,
        )
        try:
            provisioned = provision_task_workspace_clones(
                self._workspace_manager,
                self._repository_service,
                task_obj,
                repositories,
            )
        except Exception as exc:
            self.logger.exception(
                'workspace provisioning failed for adopt_task %s', normalized,
            )
            return {
                'adopted': False, 'task_id': normalized,
                'error': f'workspace provisioning failed: {exc}',
            }
        cloned_ids = [
            str(getattr(r, 'id', '') or '') for r in (provisioned or [])
        ]
        return {
            'adopted': True,
            'task_id': normalized,
            'task_summary': str(getattr(task_obj, 'summary', '') or ''),
            'cloned_repositories': [rid for rid in cloned_ids if rid],
        }




















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
        except SessionStoppedByUserError:
            # User clicked Stop — do NOT call handle_started_task_failure.
            # Moving the task back to "Open" would trigger an immediate
            # re-spawn; instead leave the task in its current state and
            # let the user decide (Resume button, manual ticket update, etc.).
            self.logger.info(
                'task %s: session stopped by user — skipping failure handler',
                task.id,
            )
            return None
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
