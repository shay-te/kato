from omegaconf import DictConfig

from openhands_agent.client.youtrack_client import YouTrackClient
from openhands_agent.data_layers.data.task import Task


class TaskDataAccess:
    def __init__(self, config: DictConfig, client: YouTrackClient) -> None:
        self.config = config
        self.client = client

    def get_assigned_tasks(self, assignee: str | None = None, state: str | None = None) -> list[Task]:
        return self.client.get_assigned_tasks(
            project=self.config.project,
            assignee=assignee or self.config.assignee,
            state=state or self.config.issue_state,
        )

    def add_pull_request_comment(self, issue_id: str, pull_request_url: str) -> None:
        self.client.add_pull_request_comment(issue_id, pull_request_url)
