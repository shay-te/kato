from omegaconf import DictConfig

from core_lib.core_lib import CoreLib

from kato.client.claude import ClaudeCliClient, ClaudeSessionManager
from kato.client.openhands import KatoClient
from kato.client.ticket_client_factory import build_ticket_client
from kato.data_layers.data_access.task_data_access import TaskDataAccess
from kato.data_layers.service.agent_service import AgentService
from kato.data_layers.service.agent_state_registry import AgentStateRegistry
from kato.data_layers.service.implementation_service import (
    ImplementationService,
)
from kato.data_layers.service.notification_service import NotificationService
from kato.data_layers.service.repository_service import RepositoryService
from kato.data_layers.service.task_failure_handler import TaskFailureHandler
from kato.data_layers.service.planning_session_runner import (
    PlanningSessionRunner,
)
from kato.data_layers.service.task_preflight_service import (
    TaskPreflightService,
)
from kato.data_layers.service.review_comment_service import (
    ReviewCommentService,
)
from kato.data_layers.service.task_publisher import TaskPublisher
from kato.data_layers.service.task_state_service import TaskStateService
from kato.data_layers.service.task_service import TaskService
from kato.data_layers.service.testing_service import TestingService
from kato.data_layers.service.parallel_task_runner import ParallelTaskRunner
from kato.data_layers.service.wait_planning_service import WaitPlanningService
from kato.data_layers.service.workspace_manager import (
    WorkspaceManager,
    provision_task_workspace_clones,
)
from kato.data_layers.service.workspace_recovery_service import (
    WorkspaceRecoveryService,
)
from kato.helpers.runtime_identity_utils import runtime_source_fingerprint
from kato.validation.branch_publishability import (
    TaskBranchPublishabilityValidator,
)
from kato.validation.branch_push import TaskBranchPushValidator
from kato.validation.model_access import TaskModelAccessValidator
from kato.validation.repository_connections import (
    RepositoryConnectionsValidator,
)
from kato.validation.startup_dependency_validator import (
    StartupDependencyValidator,
)
from kato.helpers.logging_utils import configure_logger
from kato.helpers.kato_config_utils import (
    is_claude_backend,
    resolved_agent_backend,
    resolved_openhands_base_url,
    resolved_openhands_llm_settings,
    skip_testing_enabled,
)

logger = configure_logger('KatoCoreLib')
ISSUE_PLATFORM_CONFIG_NAMES = {
    'youtrack': 'youtrack',
    'jira': 'jira',
    'github': 'github_issues',
    'github_issues': 'github_issues',
    'gitlab': 'gitlab_issues',
    'gitlab_issues': 'gitlab_issues',
    'bitbucket': 'bitbucket_issues',
    'bitbucket_issues': 'bitbucket_issues',
}


class _EmailCoreLibProxy:
    def __call__(self, *args, **kwargs):
        from email_core_lib.email_core_lib import EmailCoreLib as _EmailCoreLib

        return _EmailCoreLib(*args, **kwargs)


EmailCoreLib = _EmailCoreLibProxy()


class KatoCoreLib(CoreLib):
    def __init__(self, cfg: DictConfig) -> None:
        CoreLib.__init__(self)
        self.config = cfg
        self.logger = configure_logger(cfg.core_lib.app.name)
        self._validate_runtime_source_fingerprint(cfg.kato)
        self.service = self._build_agent_service(cfg.kato)
        self.service.validate_connections()

    def _build_agent_service(self, open_cfg: DictConfig) -> AgentService:
        retry_cfg = open_cfg.retry
        agent_backend = resolved_agent_backend(open_cfg)
        self.session_manager = ClaudeSessionManager.from_config(
            open_cfg, agent_backend,
        )
        # Per-task workspace folders (one clone-set per ticket id) are
        # backend-agnostic. Both Claude and OpenHands flows use them for
        # isolation + parallelism.
        self.workspace_manager = WorkspaceManager.from_config(
            open_cfg, agent_backend,
        )
        if self.session_manager is not None and self.workspace_manager is not None:
            self.session_manager.attach_workspace_manager(self.workspace_manager)
        # Worker pool sized to KATO_MAX_PARALLEL_TASKS. With max=1 the
        # behavior is identical to the previous synchronous loop —
        # submit-then-block — so single-task setups don't pay any cost.
        self.parallel_task_runner = ParallelTaskRunner(
            max_workers=self.workspace_manager.max_parallel_tasks,
        )
        planning_session_runner = PlanningSessionRunner.from_config(
            open_cfg, agent_backend, self.session_manager,
        )
        self.planning_session_runner = planning_session_runner
        self.logger.info('using agent backend: %s', agent_backend)
        issue_platform, ticket_cfg = self._resolve_ticket_platform_config(open_cfg)
        ticket_client = build_ticket_client(
            issue_platform,
            ticket_cfg,
            retry_cfg.max_retries,
        )
        implementation_service = ImplementationService(
            self._build_agent_client(
                open_cfg,
                retry_cfg.max_retries,
            )
        )
        testing_service = TestingService(
            self._build_agent_client(
                open_cfg,
                retry_cfg.max_retries,
                testing=True,
            )
        )
        task_data_access = TaskDataAccess(ticket_cfg, ticket_client)
        task_service = TaskService(ticket_cfg, task_data_access)
        task_state_service = TaskStateService(ticket_cfg, task_data_access)
        repository_service = RepositoryService(open_cfg, retry_cfg.max_retries)
        notification_service = self._build_notification_service(open_cfg)
        state_registry = AgentStateRegistry()
        repository_connections_validator = RepositoryConnectionsValidator(repository_service)
        startup_validator = StartupDependencyValidator(
            repository_connections_validator,
            task_service,
            implementation_service,
            testing_service,
            skip_testing_enabled(open_cfg.openhands),
            agent_backend=agent_backend,
        )
        task_model_access_validator = TaskModelAccessValidator(
            implementation_service,
        )
        task_branch_push_validator = TaskBranchPushValidator(repository_service)
        task_branch_publishability_validator = TaskBranchPublishabilityValidator(
            repository_service
        )
        # Bind the workspace provisioner here so the preflight service
        # stays free of WorkspaceManager coupling. The lambda closes over
        # the manager + repository service; calling it on a task returns
        # repos with rewritten ``local_path`` pointing at the per-task
        # workspace clones.
        workspace_provisioner = (
            (lambda task, repos: provision_task_workspace_clones(
                self.workspace_manager, repository_service, task, repos,
            ))
            if self.workspace_manager is not None
            else None
        )
        task_preflight_service = TaskPreflightService(
            task_model_access_validator=task_model_access_validator,
            task_service=task_service,
            repository_service=repository_service,
            task_branch_push_validator=task_branch_push_validator,
            task_branch_publishability_validator=task_branch_publishability_validator,
            workspace_provisioner=workspace_provisioner,
        )
        task_failure_handler = TaskFailureHandler(
            task_service=task_service,
            task_state_service=task_state_service,
            repository_service=repository_service,
            notification_service=notification_service,
        )
        task_publisher = TaskPublisher(
            task_service=task_service,
            task_state_service=task_state_service,
            repository_service=repository_service,
            notification_service=notification_service,
            state_registry=state_registry,
            failure_handler=task_failure_handler,
        )
        review_comment_service = ReviewCommentService(
            task_service=task_service,
            implementation_service=implementation_service,
            repository_service=repository_service,
            state_registry=state_registry,
            planning_session_runner=planning_session_runner,
            # Always stream review-fixes through the planning UI when the
            # streaming runner is wired (Claude backend). The user's tag
            # decides what gets executed, not bypass mode.
            use_streaming_for_review_fixes=planning_session_runner is not None,
        )
        # Stash recovery so main.py can invoke it once after startup
        # validation — adopting orphan workspace folders is opt-in, runs
        # exactly once per process, and never blocks the scan loop.
        self.workspace_recovery_service = (
            WorkspaceRecoveryService(
                workspace_manager=self.workspace_manager,
                task_service=task_service,
                repository_service=repository_service,
            )
            if self.workspace_manager is not None
            else None
        )
        return AgentService(
            task_service=task_service,
            task_state_service=task_state_service,
            implementation_service=implementation_service,
            testing_service=testing_service,
            repository_service=repository_service,
            notification_service=notification_service,
            state_registry=state_registry,
            review_comment_service=review_comment_service,
            task_failure_handler=task_failure_handler,
            task_publisher=task_publisher,
            repository_connections_validator=repository_connections_validator,
            startup_validator=startup_validator,
            task_preflight_service=task_preflight_service,
            skip_testing=skip_testing_enabled(open_cfg.openhands),
            planning_session_runner=planning_session_runner,
            session_manager=self.session_manager,
            workspace_manager=self.workspace_manager,
            parallel_task_runner=self.parallel_task_runner,
            wait_planning_service=WaitPlanningService(
                session_manager=self.session_manager,
                repository_service=repository_service,
                task_state_service=task_state_service,
                workspace_manager=self.workspace_manager,
                planning_session_runner=planning_session_runner,
            ),
        )


    @staticmethod
    def _resolve_ticket_platform_config(
        open_cfg: DictConfig,
    ) -> tuple[str, DictConfig]:
        issue_platform = str(open_cfg.issue_platform or 'youtrack').strip().lower()
        config_name = ISSUE_PLATFORM_CONFIG_NAMES.get(issue_platform)
        ticket_cfg = getattr(open_cfg, config_name, None) if config_name else None
        if ticket_cfg is None:
            raise ValueError(f'missing issue platform config for: {issue_platform}')
        return issue_platform, ticket_cfg

    def _build_notification_service(self, open_cfg: DictConfig) -> NotificationService:
        return NotificationService(
            app_name=self.config.core_lib.app.name,
            email_core_lib=EmailCoreLib(self.config),
            failure_email_cfg=open_cfg.failure_email,
            completion_email_cfg=open_cfg.completion_email,
        )

    def _validate_runtime_source_fingerprint(self, open_cfg: DictConfig) -> None:
        expected_source_fingerprint = str(open_cfg.get('source_fingerprint', '') or '').strip()
        if not expected_source_fingerprint:
            return

        current_source_fingerprint = runtime_source_fingerprint()
        if current_source_fingerprint == expected_source_fingerprint:
            return

        raise RuntimeError(
            'startup dependency validation failed: '
            'Kato source fingerprint mismatch: '
            f'expected {expected_source_fingerprint}, '
            f'got {current_source_fingerprint}; '
            'rebuild the Kato image before running'
        )

    @classmethod
    def _build_agent_client(
        cls,
        open_cfg: DictConfig,
        max_retries: int,
        *,
        testing: bool = False,
    ) -> KatoClient | ClaudeCliClient:
        if is_claude_backend(open_cfg):
            return cls._build_claude_client(open_cfg, max_retries, testing=testing)
        return cls._build_kato_client(
            open_cfg.openhands,
            max_retries,
            testing=testing,
        )

    @classmethod
    def _build_kato_client(
        cls,
        openhands_cfg: DictConfig,
        max_retries: int,
        *,
        testing: bool = False,
    ) -> KatoClient:
        return KatoClient(
            resolved_openhands_base_url(openhands_cfg, testing=testing),
            openhands_cfg.api_key,
            max_retries,
            llm_settings=resolved_openhands_llm_settings(
                openhands_cfg,
                testing=testing,
            ),
            poll_interval_seconds=cls._openhands_poll_interval_seconds(openhands_cfg),
            max_poll_attempts=cls._openhands_max_poll_attempts(openhands_cfg),
            model_smoke_test_enabled=not testing
            and bool(getattr(openhands_cfg, 'model_smoke_test_enabled', True)),
        )

    @classmethod
    def _build_claude_client(
        cls,
        open_cfg: DictConfig,
        max_retries: int,
        *,
        testing: bool = False,
    ) -> ClaudeCliClient:
        claude_cfg = getattr(open_cfg, 'claude', None)
        if claude_cfg is None:
            raise RuntimeError(
                'KATO_AGENT_BACKEND=claude requires the kato.claude config block; '
                'rebuild the configuration template'
            )
        repository_root_path = str(getattr(open_cfg, 'repository_root_path', '') or '').strip()
        return ClaudeCliClient(
            binary=str(getattr(claude_cfg, 'binary', '') or ''),
            model=str(getattr(claude_cfg, 'model', '') or ''),
            max_turns=getattr(claude_cfg, 'max_turns', None),
            effort=str(getattr(claude_cfg, 'effort', '') or ''),
            allowed_tools=str(getattr(claude_cfg, 'allowed_tools', '') or ''),
            disallowed_tools=str(getattr(claude_cfg, 'disallowed_tools', '') or ''),
            bypass_permissions=bool(getattr(claude_cfg, 'bypass_permissions', False)),
            timeout_seconds=int(getattr(claude_cfg, 'timeout_seconds', 1800) or 1800),
            max_retries=max_retries,
            repository_root_path=repository_root_path,
            model_smoke_test_enabled=(
                not testing
                and bool(getattr(claude_cfg, 'model_smoke_test_enabled', False))
            ),
            architecture_doc_path=str(
                getattr(claude_cfg, 'architecture_doc_path', '') or ''
            ),
        )

    @staticmethod
    def _openhands_poll_interval_seconds(openhands_cfg: DictConfig) -> float:
        return float(openhands_cfg.get('poll_interval_seconds', 2.0))

    @staticmethod
    def _openhands_max_poll_attempts(openhands_cfg: DictConfig) -> int:
        return int(openhands_cfg.get('max_poll_attempts', 900))
