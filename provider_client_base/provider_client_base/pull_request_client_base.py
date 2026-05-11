from __future__ import annotations

from abc import ABC, abstractmethod

from provider_client_base.provider_client_base.retrying_client_base import RetryingClientBase
from provider_client_base.provider_client_base.data.review_comment import ReviewComment
from provider_client_base.provider_client_base.data.fields import PullRequestFields, ReviewCommentFields
from provider_client_base.provider_client_base.helpers.text_utils import normalized_text


def _coerce_line_number(value: object) -> int | str:
    """Normalise a platform-supplied line number into an int (or '').

    Bitbucket / GitHub / GitLab all return integers, but JSON parsers
    occasionally hand us strings. Anything non-positive collapses to
    '' so the prompt builder can treat "no line info" uniformly.
    """
    if value is None or value == '':
        return ''
    try:
        n = int(value)
    except (TypeError, ValueError):
        return ''
    if n <= 0:
        return ''
    return n


class PullRequestClientBase(RetryingClientBase, ABC):
    provider_name = 'repository'

    @staticmethod
    def _normalized_pull_request(
        payload: object,
        *,
        id_key: str,
        url: object = '',
    ) -> dict[str, str]:
        if not isinstance(payload, dict) or id_key not in payload:
            raise ValueError('invalid pull request response payload')
        return {
            PullRequestFields.ID: normalized_text(payload[id_key]),
            PullRequestFields.TITLE: normalized_text(payload.get(PullRequestFields.TITLE, '')),
            PullRequestFields.URL: normalized_text(url),
        }

    @staticmethod
    def _review_comment_from_values(
        *,
        pull_request_id: object,
        comment_id: object,
        author: object,
        body: object,
        resolution_target_id: object = '',
        resolution_target_type: str = '',
        resolvable: bool | None = None,
        file_path: object = '',
        line_number: object = '',
        line_type: object = '',
        commit_sha: object = '',
    ) -> ReviewComment:
        comment = ReviewComment(
            pull_request_id=normalized_text(pull_request_id),
            comment_id=normalized_text(comment_id),
            author=normalized_text(author),
            body=normalized_text(body),
            file_path=normalized_text(file_path),
            line_number=_coerce_line_number(line_number),
            line_type=normalized_text(line_type),
            commit_sha=normalized_text(commit_sha),
        )
        normalized_target_id = normalized_text(resolution_target_id)
        if normalized_target_id:
            setattr(comment, ReviewCommentFields.RESOLUTION_TARGET_ID, normalized_target_id)
        if resolution_target_type:
            setattr(comment, ReviewCommentFields.RESOLUTION_TARGET_TYPE, resolution_target_type)
        setattr(
            comment,
            ReviewCommentFields.RESOLVABLE,
            bool(normalized_target_id) if resolvable is None else bool(resolvable),
        )
        return comment

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

    @abstractmethod
    def list_pull_request_comments(
        self,
        repo_owner: str,
        repo_slug: str,
        pull_request_id: str,
    ) -> list[ReviewComment]:
        raise NotImplementedError

    @abstractmethod
    def find_pull_requests(
        self,
        repo_owner: str,
        repo_slug: str,
        *,
        source_branch: str = '',
        title_prefix: str = '',
    ) -> list[dict[str, str]]:
        raise NotImplementedError

    @abstractmethod
    def reply_to_review_comment(
        self,
        repo_owner: str,
        repo_slug: str,
        comment: ReviewComment,
        body: str,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def resolve_review_comment(
        self,
        repo_owner: str,
        repo_slug: str,
        comment: ReviewComment,
    ) -> None:
        raise NotImplementedError
