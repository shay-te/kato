from omegaconf import DictConfig

from core_lib.core_lib import CoreLib

from openhands_agent.client.bitbucket_client import BitbucketClient
from openhands_agent.client.openhands_client import OpenHandsClient
from openhands_agent.client.youtrack_client import YouTrackClient
from openhands_agent.data_layers.data_access.implementation_data_access import (
    ImplementationDataAccess,
)
from openhands_agent.data_layers.data_access.pull_request_data_access import (
    PullRequestDataAccess,
)
from openhands_agent.data_layers.data_access.task_data_access import TaskDataAccess
from openhands_agent.data_layers.service.agent_service import AgentService


class OpenHandsAgentCoreLib(CoreLib):
    def __init__(self, cfg: DictConfig) -> None:
        CoreLib.__init__(self)
        self.config = cfg
        self._youtrack_client = YouTrackClient(
            cfg.openhands_agent.youtrack.base_url,
            cfg.openhands_agent.youtrack.token,
        )
        self._openhands_client = OpenHandsClient(
            cfg.openhands_agent.openhands.base_url,
            cfg.openhands_agent.openhands.api_key,
        )
        self._bitbucket_client = BitbucketClient(
            cfg.openhands_agent.bitbucket.base_url,
            cfg.openhands_agent.bitbucket.token,
        )
        self._task_data_access = TaskDataAccess(
            cfg.openhands_agent.youtrack,
            self._youtrack_client,
        )
        self._implementation_data_access = ImplementationDataAccess(
            cfg.openhands_agent.openhands,
            self._openhands_client,
        )
        self._pull_request_data_access = PullRequestDataAccess(
            cfg.openhands_agent.bitbucket,
            self._bitbucket_client,
        )
        self.agent_service = AgentService(
            task_data_access=self._task_data_access,
            implementation_data_access=self._implementation_data_access,
            pull_request_data_access=self._pull_request_data_access,
        )

    def process_assigned_tasks(self) -> list[dict[str, str]]:
        return self.agent_service.process_assigned_tasks()

    def handle_pull_request_comment(self, payload: dict) -> dict[str, str]:
        return self.agent_service.handle_pull_request_comment(payload)
