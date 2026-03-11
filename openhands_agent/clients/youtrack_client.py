from __future__ import annotations

from typing import Any

import httpx
from omegaconf import DictConfig

from openhands_agent.clients.client_base_compat import ClientBase
from openhands_agent.models.task import Task


class YouTrackClient(ClientBase):
    def __init__(self, config: DictConfig) -> None:
        super().__init__()
        self.config = config
        self.project = config.project
        self.client = httpx.Client(
            base_url=config.base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {config.token}"},
            timeout=30.0,
        )

    def get_assigned_tasks(self, assignee: str | None = None, state: str | None = None) -> list[Task]:
        assignee = assignee or self.config.assignee
        state = state or self.config.issue_state
        query = f"project: {self.project} assignee: {assignee} State: {{{state}}}"
        response = self.client.get(
            "/api/issues",
            params={"query": query, "fields": "idReadable,summary,description"},
        )
        response.raise_for_status()
        return [self._to_task(item) for item in response.json()]

    def add_pull_request_comment(self, issue_id: str, pull_request_url: str) -> None:
        response = self.client.post(
            f"/api/issues/{issue_id}/comments",
            json={"text": f"Pull request created: {pull_request_url}"},
        )
        response.raise_for_status()

    @staticmethod
    def _to_task(payload: dict[str, Any]) -> Task:
        issue_id = payload["idReadable"]
        return Task(
            id=issue_id,
            summary=payload.get("summary", ""),
            description=payload.get("description") or "",
            branch_name=f"feature/{issue_id.lower()}",
        )
