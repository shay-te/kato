from typing import Any

from core_lib.client.client_base import ClientBase

from openhands_agent.data_layers.data.task import Task


class YouTrackClient(ClientBase):
    def __init__(self, base_url: str, token: str) -> None:
        super().__init__(base_url.rstrip('/'))
        self.set_headers({'Authorization': f'Bearer {token}'})
        self.set_timeout(30)

    def get_assigned_tasks(self, project: str, assignee: str, state: str) -> list[Task]:
        query = f'project: {project} assignee: {assignee} State: {{{state}}}'
        response = self._get(
            '/api/issues',
            params={'query': query, 'fields': 'idReadable,summary,description'},
        )
        response.raise_for_status()
        return [self._to_task(item) for item in response.json()]

    def add_pull_request_comment(self, issue_id: str, pull_request_url: str) -> None:
        response = self._post(
            f'/api/issues/{issue_id}/comments',
            json={'text': f'Pull request created: {pull_request_url}'},
        )
        response.raise_for_status()

    @staticmethod
    def _to_task(payload: dict[str, Any]) -> Task:
        issue_id = payload['idReadable']
        return Task(
            id=issue_id,
            summary=payload.get(Task.summary.key, ''),
            description=payload.get(Task.description.key) or '',
            branch_name=f'feature/{issue_id.lower()}',
        )
