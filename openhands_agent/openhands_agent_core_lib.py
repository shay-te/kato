import json

from omegaconf import DictConfig

from core_lib.connection.sql_alchemy_connection_factory import (
    SqlAlchemyConnectionFactory,
)
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


class OpenHandsAgentCoreLib(CoreLib):
    def __init__(self, cfg: DictConfig) -> None:
        CoreLib.__init__(self)
        self.config = cfg
        open_cfg = cfg.openhands_agent
        retry_cfg = open_cfg.retry
        self._db_connection: SqlAlchemyConnectionFactory = (
            CoreLib.connection_factory_registry.get_or_reg(
                self.config.core_lib.data.sqlalchemy
            )
        )
        self._email_core_lib = EmailCoreLib(cfg) if hasattr(cfg.core_lib, 'email_core_lib') else None
        self._youtrack_client = YouTrackClient(
            open_cfg.youtrack.base_url,
            open_cfg.youtrack.token,
            retry_cfg.max_retries,
        )
        self._openhands_client = OpenHandsClient(
            open_cfg.openhands.base_url,
            open_cfg.openhands.api_key,
            retry_cfg.max_retries,
        )
        self._bitbucket_client = BitbucketClient(
            open_cfg.bitbucket.base_url,
            open_cfg.bitbucket.token,
            retry_cfg.max_retries,
        )
        self._task_data_access = TaskDataAccess(
            open_cfg.youtrack,
            self._youtrack_client,
        )
        self._implementation_service = ImplementationService(
            self._openhands_client,
        )
        self._pull_request_data_access = PullRequestDataAccess(
            open_cfg.bitbucket,
            self._bitbucket_client,
        )
        self.service = AgentService(
            task_data_access=self._task_data_access,
            implementation_service=self._implementation_service,
            pull_request_data_access=self._pull_request_data_access,
        )

    def notify_failure(
        self,
        operation: str,
        error: Exception,
        context: dict | None = None,
    ) -> bool:
        if not self._email_core_lib:
            return False

        failure_email_cfg = getattr(self.config.openhands_agent, 'failure_email', None)
        if not failure_email_cfg or not getattr(failure_email_cfg, 'enabled', False):
            return False

        recipients = [
            recipient
            for recipient in getattr(failure_email_cfg, 'recipients', [])
            if recipient
        ]
        template_id = getattr(failure_email_cfg, 'template_id', None)
        if not recipients or not template_id:
            return False

        sender_info = None
        sender_cfg = getattr(failure_email_cfg, 'sender', None)
        if sender_cfg:
            sender_info = {
                'name': sender_cfg.name,
                'email': sender_cfg.email,
            }

        sent = False
        for recipient in recipients:
            sent = (
                self._email_core_lib.send(
                    template_id,
                    {
                        'email': recipient,
                        'subject': f'{self.config.core_lib.app.name} failure: {operation}',
                        'operation': operation,
                        'error': str(error),
                        'context': json.dumps(context or {}, default=str),
                    },
                    sender_info,
                )
                or sent
            )
        return sent
