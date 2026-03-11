from core_lib.client.client_base import ClientBase

from openhands_agent.client.retry import is_retryable_exception, is_retryable_response
from openhands_agent.data_layers.data.review_comment import ReviewComment
from openhands_agent.data_layers.data.task import Task
from openhands_agent.fields import ImplementationFields


class OpenHandsClient(ClientBase):
    def __init__(self, base_url: str, api_key: str, max_retries: int = 3) -> None:
        super().__init__(base_url.rstrip('/'))
        self.set_headers({'Authorization': f'Bearer {api_key}'})
        self.set_timeout(300)
        self.max_retries = max(1, max_retries)

    def implement_task(self, task: Task) -> dict[str, str | bool]:
        response = self._post_with_retry(
            '/api/sessions',
            json={
                'prompt': (
                    f'Implement task {task.id}: {task.summary}\n\n'
                    f'{task.description}\n\n'
                    f'Work on branch {task.branch_name}.'
                )
            },
        )
        response.raise_for_status()
        payload = response.json() or {}
        if not isinstance(payload, dict):
            payload = {}
        return {
            Task.branch_name.key: task.branch_name,
            Task.summary.key: payload.get(Task.summary.key, ''),
            ImplementationFields.COMMIT_MESSAGE: payload.get(
                ImplementationFields.COMMIT_MESSAGE,
                f'Implement {task.id}',
            ),
            ImplementationFields.SUCCESS: bool(payload.get(ImplementationFields.SUCCESS, True)),
        }

    def fix_review_comment(self, comment: ReviewComment, branch_name: str) -> dict[str, str | bool]:
        response = self._post_with_retry(
            '/api/sessions',
            json={
                'prompt': (
                    f'Address pull request comment on branch {branch_name}.\n'
                    f'Comment by {comment.author}: {comment.body}'
                )
            },
        )
        response.raise_for_status()
        payload = response.json() or {}
        if not isinstance(payload, dict):
            payload = {}
        return {
            Task.branch_name.key: branch_name,
            Task.summary.key: payload.get(Task.summary.key, ''),
            ImplementationFields.COMMIT_MESSAGE: payload.get(
                ImplementationFields.COMMIT_MESSAGE,
                'Address review comments',
            ),
            ImplementationFields.SUCCESS: bool(payload.get(ImplementationFields.SUCCESS, True)),
        }

    def _post_with_retry(self, path: str, **kwargs):
        last_response = None
        for attempt in range(self.max_retries):
            try:
                response = self._post(path, **kwargs)
            except Exception as exc:
                if attempt == self.max_retries - 1 or not is_retryable_exception(exc):
                    raise
                continue

            last_response = response
            if attempt < self.max_retries - 1 and is_retryable_response(response):
                continue
            return response

        return last_response
