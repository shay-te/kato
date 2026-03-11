from typing import Any

from core_lib.client.client_base import ClientBase

from openhands_agent.client.retry import is_retryable_exception, is_retryable_response
from openhands_agent.fields import PullRequestFields


class BitbucketClient(ClientBase):
    def __init__(self, base_url: str, token: str, max_retries: int = 3) -> None:
        super().__init__(base_url.rstrip('/'))
        self.set_headers({'Authorization': f'Bearer {token}'})
        self.set_timeout(30)
        self.max_retries = max(1, max_retries)

    def create_pull_request(
        self,
        title: str,
        source_branch: str,
        workspace: str,
        repo_slug: str,
        destination_branch: str | None = None,
        description: str = '',
    ) -> dict[str, str]:
        response = self._post_with_retry(
            f'/repositories/{workspace}/{repo_slug}/pullrequests',
            json={
                PullRequestFields.TITLE: title,
                PullRequestFields.DESCRIPTION: description,
                'source': {'branch': {'name': source_branch}},
                'destination': {'branch': {'name': destination_branch}},
            },
        )
        response.raise_for_status()
        return self._normalize_pr(response.json())

    @staticmethod
    def _normalize_pr(payload: dict[str, Any]) -> dict[str, str]:
        if not isinstance(payload, dict) or PullRequestFields.ID not in payload:
            raise ValueError('invalid pull request response payload')
        return {
            PullRequestFields.ID: str(payload[PullRequestFields.ID]),
            PullRequestFields.TITLE: payload.get(PullRequestFields.TITLE, ''),
            PullRequestFields.URL: payload.get('links', {}).get('html', {}).get('href', ''),
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
