"""Regression: ``get_assigned_tasks`` paginates beyond the first 100.

Before the fix, a team with >100 in-progress issues silently lost
issues 101+ — kato would never pick them up. The fix iterates
``$top`` + ``$skip`` until a short page tells us we're at the end.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from youtrack_core_lib.youtrack_core_lib.client.youtrack_client import YouTrackClient
from youtrack_core_lib.youtrack_core_lib.tests.test_youtrack_client import (
    get_assigned_tasks_with_defaults,
    mock_response,
)


def _issue(idx: int):
    return {'idReadable': f'PROJ-{idx}', 'summary': f'Issue {idx}', 'description': ''}


class YouTrackPaginationTests(unittest.TestCase):

    def test_single_page_under_page_size_stops(self) -> None:
        # 5 issues fit in one page; no second request fires.
        client = YouTrackClient('https://youtrack.example', 'yt-token')
        first_page = mock_response(json_data=[_issue(i) for i in range(1, 6)])
        empty_meta = mock_response(json_data=[])

        with patch.object(
            client, '_get',
            # Issues page + then per-issue tags/comments/attachments
            # for each of the 5 issues (3 each). Keep it simple:
            # short page → stop → enrichment for each issue.
            side_effect=[first_page] + [empty_meta] * (5 * 3),
        ) as mock_get:
            tasks = get_assigned_tasks_with_defaults(client)

        # 5 tasks returned, exactly 1 issues-page call.
        self.assertEqual(len(tasks), 5)
        issue_calls = [
            c for c in mock_get.call_args_list
            if c.args and c.args[0] == '/api/issues'
        ]
        self.assertEqual(len(issue_calls), 1)
        # The page request carries $skip=0.
        self.assertEqual(issue_calls[0].kwargs['params']['$skip'], 0)

    def test_multi_page_loops_until_short_page(self) -> None:
        # Page 1 is full (100 issues), page 2 is partial (5). Expect
        # two page requests with $skip=0 then $skip=100, then stop.
        client = YouTrackClient('https://youtrack.example', 'yt-token')
        full_page = mock_response(json_data=[_issue(i) for i in range(1, 101)])
        partial_page = mock_response(json_data=[_issue(i) for i in range(101, 106)])
        # 100 + 5 = 105 issues total, each needs 3 enrichment GETs.
        empty_meta = mock_response(json_data=[])

        with patch.object(
            client, '_get',
            side_effect=[full_page, partial_page] + [empty_meta] * (105 * 3),
        ) as mock_get:
            tasks = get_assigned_tasks_with_defaults(client)

        self.assertEqual(len(tasks), 105)
        issue_calls = [
            c for c in mock_get.call_args_list
            if c.args and c.args[0] == '/api/issues'
        ]
        # TWO page requests, with the correct skip values.
        self.assertEqual(len(issue_calls), 2)
        self.assertEqual(issue_calls[0].kwargs['params']['$skip'], 0)
        self.assertEqual(issue_calls[1].kwargs['params']['$skip'], 100)


if __name__ == '__main__':
    unittest.main()
