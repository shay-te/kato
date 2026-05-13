"""Regression: GitLab discussions paginate via X-Next-Page header.

Before the fix, MRs with >100 discussions silently truncated at
the first page. Kato never saw comments 101+.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from gitlab_core_lib.gitlab_core_lib.client.gitlab_client import GitLabClient
from gitlab_core_lib.gitlab_core_lib.tests.test_gitlab_client import mock_response


def _discussion(idx: int):
    return {
        'id': f'd-{idx}',
        'resolved': False,
        'notes': [{
            'id': 100 + idx,
            'body': f'comment {idx}',
            'author': {'username': 'reviewer'},
        }],
    }


def _with_next_page(payload, next_page_value):
    """Wrap a mock response so .headers carries an X-Next-Page value."""
    response = mock_response(json_data=payload)
    response.headers = {'X-Next-Page': str(next_page_value or '')}
    return response


class GitLabPaginationTests(unittest.TestCase):

    def test_single_page_no_next_header_stops(self) -> None:
        client = GitLabClient('https://gitlab.example/api/v4', 'gl-token')
        page1 = _with_next_page([_discussion(i) for i in range(1, 6)], '')

        with patch.object(client, '_get', return_value=page1) as mock_get:
            client.list_pull_request_comments('group', 'repo', '17')

        self.assertEqual(mock_get.call_count, 1)
        self.assertEqual(mock_get.call_args.kwargs['params']['page'], 1)

    def test_multi_page_follows_next_until_blank(self) -> None:
        client = GitLabClient('https://gitlab.example/api/v4', 'gl-token')
        page1 = _with_next_page([_discussion(i) for i in range(1, 101)], '2')
        page2 = _with_next_page([_discussion(i) for i in range(101, 106)], '')

        with patch.object(client, '_get', side_effect=[page1, page2]) as mock_get:
            comments = client.list_pull_request_comments('group', 'repo', '17')

        # 105 discussions, each producing one comment.
        self.assertEqual(len(comments), 105)
        self.assertEqual(mock_get.call_count, 2)
        self.assertEqual(mock_get.call_args_list[0].kwargs['params']['page'], 1)
        self.assertEqual(mock_get.call_args_list[1].kwargs['params']['page'], 2)

    def test_malformed_next_page_value_stops_loop(self) -> None:
        # Defensive: if the server returns "X-Next-Page: garbage",
        # we stop rather than crash on int() conversion.
        client = GitLabClient('https://gitlab.example/api/v4', 'gl-token')
        page1 = _with_next_page([_discussion(1)], 'not-a-number')

        with patch.object(client, '_get', return_value=page1) as mock_get:
            client.list_pull_request_comments('group', 'repo', '17')

        self.assertEqual(mock_get.call_count, 1)


if __name__ == '__main__':
    unittest.main()
