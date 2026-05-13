"""Adversarial regression test for kato bug:
``_unprocessed_review_comments`` returns comments in REVERSE chronological
order despite the documented contract.

The function de-duplicates per-thread by walking backwards (so the newest
comment per thread is kept). But it then ``append``s to ``new_comments``,
producing output in reverse-chronological order.

Contract violated (from
`kato_core_lib/jobs/process_assigned_tasks.py:_group_review_comments_by_pull_request`):

    "Order within a bucket preserves the order
    ``get_new_pull_request_comments`` returned — which is roughly
    chronological — so the agent sees comments in the same order the
    reviewer wrote them."

Why this matters: when an agent receives a batch where comment B
depends on comment A ("Fix the null check" → later "Actually, never
mind"), processing B first leads to incorrect behavior. The order
contract is load-bearing for agent correctness.

This test creates 3 chronologically-ordered comments and asserts the
output is in the SAME order, not reversed.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from kato_core_lib.data_layers.data.fields import PullRequestFields
from kato_core_lib.data_layers.service.review_comment_service import (
    ReviewCommentService,
)


def _make_comment(comment_id, body='question?', thread_id=None):
    # Each comment has a unique thread (resolution_key uses the
    # ``comment_id`` of the parent, or its own id when not a reply).
    return SimpleNamespace(
        comment_id=comment_id,
        body=body,
        pull_request_id='pr-1',
        repository_id='repo-1',
        author='reviewer',
        file_path='auth.py',
        line_number=10,
        line_type='ADDED',
        commit_sha='abc1234',
        parent_id=thread_id or comment_id,
    )


def _make_service():
    service = ReviewCommentService.__new__(ReviewCommentService)
    service.logger = MagicMock()
    service._repository_service = MagicMock()
    service._task_service = MagicMock()
    service._state_registry = MagicMock()
    service._state_registry.is_review_comment_processed.return_value = False
    return service


class BugReviewCommentChronologicalOrderTests(unittest.TestCase):

    def test_unprocessed_review_comments_preserves_chronological_order(self) -> None:
        # Three comments in chronological order, each on its own thread
        # (so no dedup interference). The output MUST be in the same
        # order as the input.
        service = _make_service()
        # ``_is_review_comment_processed`` is the gate; bypass it.
        service._is_review_comment_processed = MagicMock(return_value=False)

        comments = [
            _make_comment('c1', body='How does X work?', thread_id='t1'),
            _make_comment('c2', body='Why is Y so slow?', thread_id='t2'),
            _make_comment('c3', body='What about Z?', thread_id='t3'),
        ]
        comment_context = [{'id': c.comment_id} for c in comments]

        result = service._unprocessed_review_comments(
            comments,
            repository_id='repo-1',
            pull_request_id='pr-1',
            comment_context=comment_context,
        )

        # All three returned (none filtered).
        self.assertEqual(len(result), 3)
        # Order MUST match input order — operator's documented contract.
        result_ids = [c.comment_id for c in result]
        self.assertEqual(
            result_ids, ['c1', 'c2', 'c3'],
            f'review comments came back in {result_ids} — but the '
            f'documented contract says they should be in chronological '
            f'order [c1, c2, c3]. The agent will address them in the '
            f'wrong order, potentially making decisions on later '
            f'comments before context from earlier ones.',
        )


if __name__ == '__main__':
    unittest.main()
