"""Final coverage for provider_client_base — abstract method bodies +
retry_utils naive-datetime branch."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from provider_client_base.provider_client_base.pull_request_client_base import (
    PullRequestClientBase,
)


class _ConcretePullRequestClient(PullRequestClientBase):
    """Minimal concrete subclass that defers to the abstract bodies."""

    def validate_connection(self, repo_owner, repo_slug):
        return super().validate_connection(repo_owner, repo_slug)

    def create_pull_request(self, title, source_branch, repo_owner, repo_slug,
                            destination_branch=None, description=''):
        return super().create_pull_request(
            title, source_branch, repo_owner, repo_slug,
            destination_branch, description,
        )

    def list_pull_request_comments(self, repo_owner, repo_slug, pull_request_id):
        return super().list_pull_request_comments(
            repo_owner, repo_slug, pull_request_id,
        )

    def find_pull_requests(self, repo_owner, repo_slug, *,
                           source_branch='', title_prefix=''):
        return super().find_pull_requests(
            repo_owner, repo_slug,
            source_branch=source_branch, title_prefix=title_prefix,
        )

    def reply_to_review_comment(self, repo_owner, repo_slug, comment, body):
        return super().reply_to_review_comment(
            repo_owner, repo_slug, comment, body,
        )

    def resolve_review_comment(self, repo_owner, repo_slug, comment):
        return super().resolve_review_comment(repo_owner, repo_slug, comment)


def _build_client() -> _ConcretePullRequestClient:
    return _ConcretePullRequestClient(
        base_url='https://example.com', token='t', timeout=10.0,
    )


class AbstractMethodBodiesTests(unittest.TestCase):
    """Every abstract method's body must raise NotImplementedError when
    a concrete subclass calls ``super()``. Locks the contract so a
    subclass that forgets to override surfaces the omission."""

    def test_validate_connection_raises(self) -> None:
        with self.assertRaises(NotImplementedError):
            _build_client().validate_connection('o', 'r')

    def test_create_pull_request_raises(self) -> None:
        with self.assertRaises(NotImplementedError):
            _build_client().create_pull_request('t', 's', 'o', 'r')

    def test_list_pull_request_comments_raises(self) -> None:
        with self.assertRaises(NotImplementedError):
            _build_client().list_pull_request_comments('o', 'r', '1')

    def test_find_pull_requests_raises(self) -> None:
        with self.assertRaises(NotImplementedError):
            _build_client().find_pull_requests('o', 'r')

    def test_reply_to_review_comment_raises(self) -> None:
        from provider_client_base.provider_client_base.data.review_comment import (
            ReviewComment,
        )
        comment = ReviewComment(
            pull_request_id='1', comment_id='c1', author='a', body='b',
        )
        with self.assertRaises(NotImplementedError):
            _build_client().reply_to_review_comment('o', 'r', comment, 'body')

    def test_resolve_review_comment_raises(self) -> None:
        from provider_client_base.provider_client_base.data.review_comment import (
            ReviewComment,
        )
        comment = ReviewComment(
            pull_request_id='1', comment_id='c1', author='a', body='b',
        )
        with self.assertRaises(NotImplementedError):
            _build_client().resolve_review_comment('o', 'r', comment)


class RetryAfterNaiveTimezoneTests(unittest.TestCase):
    def test_retry_loop_returns_none_when_max_retries_zero(self) -> None:
        # Line 78: ``return last_response`` — only reachable when the
        # for loop never iterates (max_retries=0). last_response is
        # still None at that point.
        from provider_client_base.provider_client_base.helpers.retry_utils import (
            run_with_retry,
        )
        from unittest.mock import MagicMock
        op = MagicMock()
        result = run_with_retry(op, 0)
        self.assertIsNone(result)
        op.assert_not_called()

    def test_replaces_naive_tz_with_utc(self) -> None:
        # Line 110: ``retry_after_time.replace(tzinfo=timezone.utc)``.
        from provider_client_base.provider_client_base.helpers import retry_utils
        naive_future = datetime.utcnow() + timedelta(seconds=5)
        with patch.object(
            retry_utils, 'parsedate_to_datetime', return_value=naive_future,
        ):
            response = SimpleNamespace(
                status_code=429,
                headers={'Retry-After': 'irrelevant'},
            )
            delay = retry_utils._retry_after_seconds(response)
        self.assertGreaterEqual(delay, 0.0)
        self.assertLessEqual(delay, 10.0)


if __name__ == '__main__':
    unittest.main()
