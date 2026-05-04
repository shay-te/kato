from __future__ import annotations

from repository_core_lib.repository_core_lib.client.pull_request_client_factory import (
    PullRequestClientFactory,
)
from repository_core_lib.repository_core_lib.repository_type import RepositoryType


class PullRequestService(object):
    """Route repository pull-request operations to the configured provider."""

    provider_name = 'repository'

    def __init__(self, factory: PullRequestClientFactory) -> None:
        self._factory = factory

    def validate_connection(
        self,
        repository_type: RepositoryType,
        *,
        repo_owner: str,
        repo_slug: str,
    ) -> None:
        client = self._factory.get(repository_type)
        client.validate_connection(
            repo_owner=repo_owner,
            repo_slug=repo_slug,
        )

    def create_pull_request(
        self,
        repository_type: RepositoryType,
        *,
        title: str,
        source_branch: str,
        repo_owner: str,
        repo_slug: str,
        destination_branch: str | None = None,
        description: str = '',
    ) -> dict[str, str]:
        client = self._factory.get(repository_type)
        return client.create_pull_request(
            title=title,
            source_branch=source_branch,
            repo_owner=repo_owner,
            repo_slug=repo_slug,
            destination_branch=destination_branch,
            description=description,
        )

    def list_pull_request_comments(
        self,
        repository_type: RepositoryType,
        *,
        repo_owner: str,
        repo_slug: str,
        pull_request_id: str,
    ) -> list[dict[str, str]]:
        client = self._factory.get(repository_type)
        return client.list_pull_request_comments(
            repo_owner=repo_owner,
            repo_slug=repo_slug,
            pull_request_id=pull_request_id,
        )

    def find_pull_requests(
        self,
        repository_type: RepositoryType,
        *,
        repo_owner: str,
        repo_slug: str,
        source_branch: str = '',
        title_prefix: str = '',
    ) -> list[dict[str, str]]:
        client = self._factory.get(repository_type)
        return client.find_pull_requests(
            repo_owner=repo_owner,
            repo_slug=repo_slug,
            source_branch=source_branch,
            title_prefix=title_prefix,
        )

    def reply_to_review_comment(
        self,
        repository_type: RepositoryType,
        *,
        repo_owner: str,
        repo_slug: str,
        comment,
        body: str,
    ) -> None:
        client = self._factory.get(repository_type)
        client.reply_to_review_comment(
            repo_owner=repo_owner,
            repo_slug=repo_slug,
            comment=comment,
            body=body,
        )

    def resolve_review_comment(
        self,
        repository_type: RepositoryType,
        *,
        repo_owner: str,
        repo_slug: str,
        comment,
    ) -> None:
        client = self._factory.get(repository_type)
        client.resolve_review_comment(
            repo_owner=repo_owner,
            repo_slug=repo_slug,
            comment=comment,
        )
