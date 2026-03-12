from typing import Any

from openhands_agent.client.pull_request_client_base import PullRequestClientBase
from openhands_agent.fields import PullRequestFields


class BitbucketClient(PullRequestClientBase):
    provider_name = 'bitbucket'

    def __init__(self, base_url: str, token: str, max_retries: int = 3) -> None:
        super().__init__(base_url, token, timeout=30, max_retries=max_retries)

    def validate_connection(self, repo_owner: str, repo_slug: str) -> None:
        response = self._get_with_retry(f'/repositories/{repo_owner}/{repo_slug}')
        response.raise_for_status()

    def create_pull_request(
        self,
        title: str,
        source_branch: str,
        repo_owner: str,
        repo_slug: str,
        destination_branch: str | None = None,
        description: str = '',
    ) -> dict[str, str]:
        response = self._post_with_retry(
            f'/repositories/{repo_owner}/{repo_slug}/pullrequests',
            json=self._pull_request_payload(
                title=title,
                source_branch=source_branch,
                destination_branch=destination_branch,
                description=description,
            ),
        )
        response.raise_for_status()
        return self._normalize_pr(response.json())

    @staticmethod
    def _pull_request_payload(
        title: str,
        source_branch: str,
        destination_branch: str | None,
        description: str,
    ) -> dict[str, Any]:
        return {
            PullRequestFields.TITLE: title,
            PullRequestFields.DESCRIPTION: description,
            'source': {'branch': {'name': source_branch}},
            'destination': {'branch': {'name': destination_branch}},
        }

    @staticmethod
    def _normalize_pr(payload: dict[str, Any]) -> dict[str, str]:
        if not isinstance(payload, dict) or PullRequestFields.ID not in payload:
            raise ValueError('invalid pull request response payload')
        links = payload.get('links')
        if not isinstance(links, dict):
            links = {}
        html_link = links.get('html')
        if not isinstance(html_link, dict):
            html_link = {}
        return {
            PullRequestFields.ID: str(payload[PullRequestFields.ID]),
            PullRequestFields.TITLE: str(payload.get(PullRequestFields.TITLE, '')),
            PullRequestFields.URL: str(html_link.get('href', '')),
        }
