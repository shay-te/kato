from __future__ import annotations

from typing import Protocol, runtime_checkable

from vcs_provider_contracts.pull_request import PullRequest
from vcs_provider_contracts.review_comment import ReviewComment


@runtime_checkable
class PullRequestProvider(Protocol):
    def validate_connection(self, repo_owner: str, repo_slug: str) -> None:
        raise NotImplementedError

    def create_pull_request(
        self,
        title: str,
        source_branch: str,
        repo_owner: str,
        repo_slug: str,
        destination_branch: str | None = None,
        description: str = '',
    ) -> PullRequest:
        raise NotImplementedError

    def list_pull_request_comments(
        self,
        repo_owner: str,
        repo_slug: str,
        pull_request_id: str,
    ) -> list[ReviewComment]:
        raise NotImplementedError

    def find_pull_requests(
        self,
        repo_owner: str,
        repo_slug: str,
        *,
        source_branch: str = '',
        title_prefix: str = '',
    ) -> list[PullRequest]:
        raise NotImplementedError

    def reply_to_review_comment(
        self,
        repo_owner: str,
        repo_slug: str,
        comment: ReviewComment,
        body: str,
    ) -> None:
        raise NotImplementedError

    def resolve_review_comment(
        self,
        repo_owner: str,
        repo_slug: str,
        comment: ReviewComment,
    ) -> None:
        raise NotImplementedError
