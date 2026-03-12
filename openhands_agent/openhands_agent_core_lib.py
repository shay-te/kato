from omegaconf import DictConfig

from core_lib.core_lib import CoreLib
from email_core_lib.email_core_lib import EmailCoreLib

from openhands_agent.client.bitbucket_client import BitbucketClient
from openhands_agent.client.openhands_client import OpenHandsClient
from openhands_agent.client.youtrack_client import YouTrackClient
from openhands_agent.data_layers.data_access.pull_request_data_access import (
    PullRequestDataAccess,
)
from openhands_agent.data_layers.data_access.task_data_access import TaskDataAccess
from openhands_agent.data_layers.service.agent_service import AgentService
from openhands_agent.data_layers.service.implementation_service import (
    ImplementationService,
)
from openhands_agent.data_layers.service.notification_service import NotificationService
from openhands_agent.logging_utils import configure_logger


class OpenHandsAgentCoreLib(CoreLib):
    def __init__(self, cfg: DictConfig) -> None:
        CoreLib.__init__(self)
        self.config = cfg
        self.logger = configure_logger(cfg.core_lib.app.name)
        open_cfg = cfg.openhands_agent
        retry_cfg = open_cfg.retry

        CoreLib.connection_factory_registry.get_or_reg(self.config.core_lib.data.sqlalchemy)
        _email_core_lib = EmailCoreLib(cfg) if hasattr(cfg.core_lib, 'email_core_lib') else None
        _youtrack_client = YouTrackClient(open_cfg.youtrack.base_url, open_cfg.youtrack.token, retry_cfg.max_retries)
        _openhands_client = OpenHandsClient(
            open_cfg.openhands.base_url,
            open_cfg.openhands.api_key,
            retry_cfg.max_retries,
            getattr(open_cfg.openhands, 'pre_pull_request_commands', None),
        )
        _bitbucket_client = BitbucketClient(open_cfg.bitbucket.base_url, open_cfg.bitbucket.token, retry_cfg.max_retries)
        _task_data_access = TaskDataAccess(open_cfg.youtrack, _youtrack_client)
        _implementation_service = ImplementationService(_openhands_client)
        _pull_request_data_access = PullRequestDataAccess(open_cfg.bitbucket, _bitbucket_client)
        notification_service = NotificationService(app_name=self.config.core_lib.app.name, email_core_lib=_email_core_lib, failure_email_cfg=getattr(open_cfg, 'failure_email', None), completion_email_cfg=getattr(open_cfg, 'completion_email', None))
        self.service = AgentService(
            task_data_access=_task_data_access,
            implementation_service=_implementation_service,
            pull_request_data_access=_pull_request_data_access,
            notification_service=notification_service,
        )
        self.service.validate_connections()
