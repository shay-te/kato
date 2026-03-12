from abc import ABC, abstractmethod

from openhands_agent.client.retrying_client_base import RetryingClientBase


class PullRequestClientBase(RetryingClientBase, ABC):
    provider_name = 'repository'

    @abstractmethod
    def validate_connection(self, repo_owner: str, repo_slug: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def create_pull_request(
        self,
        title: str,
        source_branch: str,
        repo_owner: str,
        repo_slug: str,
        destination_branch: str | None = None,
        description: str = '',
    ) -> dict[str, str]:
        raise NotImplementedError
