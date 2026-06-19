"""End-to-end flow tests for the GitHub clients — A-Z scenarios.

Each test class represents one named flow and exercises the full call chain
from a public method call down through mocked HTTP responses back to the
structured result.  No internal parsing/normalisation methods are patched;
only the lowest-level transport is intercepted so the full parsing, retry,
pagination, and assembly logic runs:

* REST  — ``_get`` / ``_post`` / ``_patch`` / ``_delete`` (the verbs the
  ``_*_with_retry`` wrappers call) are stubbed with mocked responses.
* GraphQL — ``session.post`` (the call ``_graphql_with_retry`` makes) is
  stubbed, so cursor pagination and the null-``data`` guards execute.

The library is exercised through its real public surface:
``GitHubIssuesClient`` (validate → list assigned issues → comment → label/
move) and ``GitHubClient`` (open PR → list review comments → reply → resolve
thread). All data is generic example owner/repo/user/issue content.
"""
from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from provider_client_base.provider_client_base.data.fields import (
    PullRequestFields,
    ReviewCommentFields,
)
from provider_client_base.provider_client_base.data.issue_record import IssueRecord
from provider_client_base.provider_client_base.data.review_comment import ReviewComment

from github_core_lib.github_core_lib.client.github_client import GitHubClient
from github_core_lib.github_core_lib.client.github_issues_client import (
    GitHubIssuesClient,
)
from github_core_lib.github_core_lib.data.fields import (
    ISSUE_ALL_COMMENTS,
    GitHubCommentFields,
    GitHubIssueFields,
)

BASE_URL = 'https://api.github.com'
TOKEN = 'gh-token'
OWNER = 'acme'
REPO = 'widget'


class ClientTimeout(TimeoutError):
    """Simulated transient network timeout (one retryable failure)."""


def mock_response(*, json_data=None, status_code: int = 200, text: str = '') -> Mock:
    response = Mock(status_code=status_code)
    response.json.return_value = json_data
    response.text = text
    return response


def _issues_client(**kwargs) -> GitHubIssuesClient:
    return GitHubIssuesClient(BASE_URL, TOKEN, OWNER, REPO, max_retries=2, **kwargs)


def _pr_client(**kwargs) -> GitHubClient:
    return GitHubClient(BASE_URL, TOKEN, max_retries=2, **kwargs)


def _review_threads_payload(nodes, *, has_next_page: bool = False, end_cursor=None):
    return {
        'data': {
            'repository': {
                'pullRequest': {
                    'reviewThreads': {
                        'pageInfo': {
                            'hasNextPage': has_next_page,
                            'endCursor': end_cursor,
                        },
                        'nodes': nodes,
                    }
                }
            }
        }
    }


# ---------------------------------------------------------------------------
# F1 — Issue lifecycle A-Z: validate → list assigned → comment → move
# ---------------------------------------------------------------------------
class F1_IssueLifecycle(unittest.TestCase):
    """Full issue flow: connect, pull the assigned backlog, then act on one.

    A. construct the issues client (Bearer auth + Accept header baked in)
    B. validate_connection hits the issues endpoint and raises on error
    C. get_assigned_tasks lists issues, drops PRs + out-of-state items,
       folds human comments into the description, maps labels → tags
    D. add_comment posts a progress note
    E. move_issue_to_state (labels field) applies the "In Review" label
    """

    def test_flow(self):
        client = _issues_client(
            is_operational_comment=lambda body: body.startswith('[bot]'),
        )

        # A — auth wiring is applied at construction.
        self.assertEqual(
            client.headers,
            {
                'Authorization': f'Bearer {TOKEN}',
                'Accept': 'application/vnd.github+json',
            },
        )
        self.assertEqual(client.timeout, 30)

        issues_payload = [
            {
                GitHubIssueFields.NUMBER: 101,
                GitHubIssueFields.TITLE: 'Add retry to uploader',
                GitHubIssueFields.BODY: 'Uploads fail on flaky networks.',
                GitHubIssueFields.STATE: 'open',
                GitHubIssueFields.LABELS: [
                    {GitHubIssueFields.NAME: 'reliability'},
                    {GitHubIssueFields.NAME: 'backend'},
                ],
            },
            {
                GitHubIssueFields.NUMBER: 102,
                GitHubIssueFields.TITLE: 'A pull request, not an issue',
                GitHubIssueFields.BODY: '',
                GitHubIssueFields.STATE: 'open',
                GitHubIssueFields.LABELS: [],
                GitHubIssueFields.PULL_REQUEST: {
                    'url': 'https://api.github.com/pulls/102'
                },
            },
            {
                GitHubIssueFields.NUMBER: 103,
                GitHubIssueFields.TITLE: 'Closed already',
                GitHubIssueFields.BODY: '',
                GitHubIssueFields.STATE: 'closed',
                GitHubIssueFields.LABELS: [],
            },
        ]
        comments_payload = [
            {
                GitHubCommentFields.BODY: '[bot] automated triage ran',
                GitHubCommentFields.USER: {GitHubCommentFields.LOGIN: 'ci-bot'},
            },
            {
                GitHubCommentFields.BODY: 'Please cap retries at 3.',
                GitHubCommentFields.USER: {GitHubCommentFields.LOGIN: 'maria'},
            },
        ]

        validate_resp = mock_response(json_data=[])
        issues_resp = mock_response(json_data=issues_payload)
        comments_resp = mock_response(json_data=comments_payload)

        def get_side_effect(path, **kwargs):
            if path.endswith('/issues') and kwargs.get('params', {}).get('per_page') == 1:
                return validate_resp
            if path.endswith('/issues'):
                return issues_resp
            if path.endswith('/comments'):
                return comments_resp
            return mock_response(json_data=[])

        with patch.object(client, '_get', side_effect=get_side_effect) as mock_get:
            # B — validate.
            client.validate_connection(REPO, 'maria', ['open'])
            validate_resp.raise_for_status.assert_called_once_with()

            # C — list assigned.
            records = client.get_assigned_tasks(REPO, 'maria', ['open'])

        # The first GET is the validate probe; the second is the list query.
        list_call = mock_get.call_args_list[1]
        self.assertEqual(list_call.args[0], f'/repos/{OWNER}/{REPO}/issues')
        self.assertEqual(
            list_call.kwargs['params'],
            {
                'assignee': 'maria',
                'state': 'all',
                'sort': 'updated',
                'direction': 'desc',
                'per_page': 100,
            },
        )

        # Only issue 101 survives: not a PR, and state is open.
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertIsInstance(record, IssueRecord)
        self.assertEqual(record.id, '101')
        self.assertEqual(record.summary, 'Add retry to uploader')
        self.assertEqual(record.branch_name, 'feature/101')
        self.assertEqual(record.tags, ['reliability', 'backend'])
        # Body + human comment in description; operational comment excluded.
        self.assertIn('Uploads fail on flaky networks.', record.description)
        self.assertIn('maria: Please cap retries at 3.', record.description)
        self.assertNotIn('[bot] automated triage ran', record.description)
        # Both raw comments are preserved on the record.
        self.assertEqual(len(getattr(record, ISSUE_ALL_COMMENTS)), 2)

        # D — post a progress comment.
        with patch.object(client, '_post', return_value=mock_response()) as mock_post:
            client.add_comment(record.id, 'Working on this now.')
        mock_post.assert_called_once_with(
            f'/repos/{OWNER}/{REPO}/issues/101/comments',
            json={GitHubCommentFields.BODY: 'Working on this now.'},
        )

        # E — move to review by applying a label.
        with patch.object(client, '_post', return_value=mock_response()) as mock_label:
            client.move_issue_to_state(record.id, 'labels', 'In Review')
        mock_label.assert_called_once_with(
            f'/repos/{OWNER}/{REPO}/issues/101/labels',
            json={GitHubIssueFields.LABELS: ['In Review']},
        )


# ---------------------------------------------------------------------------
# F2 — Pull-request review flow A-Z: open PR → list comments → reply → resolve
# ---------------------------------------------------------------------------
class F2_PullRequestReviewFlow(unittest.TestCase):
    """Full PR flow against the REST + GraphQL transports.

    A. validate_connection probes the repo
    B. create_pull_request POSTs head/base/body and normalises the response
    C. list_pull_request_comments reads unresolved review threads (GraphQL)
       and parses them into ReviewComment objects with inline metadata
    D. reply_to_review_comment POSTs a threaded REST reply
    E. resolve_review_comment fires the resolveReviewThread mutation
    """

    def test_flow(self):
        client = _pr_client()

        # A — validate.
        with patch.object(client, '_get', return_value=mock_response()) as mock_get:
            client.validate_connection(OWNER, REPO)
        mock_get.assert_called_once_with(f'/repos/{OWNER}/{REPO}')

        # B — open a pull request.
        create_resp = mock_response(
            json_data={
                'number': 42,
                PullRequestFields.TITLE: 'Add retry to uploader',
                'html_url': f'https://github.com/{OWNER}/{REPO}/pull/42',
            }
        )
        with patch.object(client, '_post', return_value=create_resp) as mock_post:
            pr = client.create_pull_request(
                title='Add retry to uploader',
                source_branch='feature/101',
                repo_owner=OWNER,
                repo_slug=REPO,
                destination_branch='main',
                description='Closes #101',
            )
        mock_post.assert_called_once_with(
            f'/repos/{OWNER}/{REPO}/pulls',
            json={
                PullRequestFields.TITLE: 'Add retry to uploader',
                'head': 'feature/101',
                'base': 'main',
                'body': 'Closes #101',
            },
        )
        self.assertEqual(
            pr,
            {
                PullRequestFields.ID: '42',
                PullRequestFields.TITLE: 'Add retry to uploader',
                PullRequestFields.URL: f'https://github.com/{OWNER}/{REPO}/pull/42',
            },
        )

        # C — read the open review threads via GraphQL.
        graphql_resp = mock_response(
            json_data=_review_threads_payload(
                [
                    {
                        'id': 'thread-1',
                        'isResolved': False,
                        'path': 'src/uploader.py',
                        'line': 27,
                        'originalLine': 25,
                        'comments': {
                            'nodes': [
                                {
                                    'databaseId': 9001,
                                    'body': 'Cap the backoff so it cannot grow forever.',
                                    'author': {'login': 'reviewer'},
                                    'commit': {'oid': 'abc123'},
                                }
                            ]
                        },
                    },
                    {
                        # Resolved threads are skipped.
                        'id': 'thread-2',
                        'isResolved': True,
                        'comments': {
                            'nodes': [
                                {
                                    'databaseId': 9002,
                                    'body': 'Already handled.',
                                    'author': {'login': 'reviewer'},
                                }
                            ]
                        },
                    },
                ]
            )
        )
        with patch.object(
            client.session, 'post', return_value=graphql_resp
        ) as mock_graphql_post:
            comments = client.list_pull_request_comments(OWNER, REPO, str(pr[PullRequestFields.ID]))

        # GraphQL hit the derived /graphql endpoint with the PR number.
        graphql_call = mock_graphql_post.call_args
        self.assertEqual(graphql_call.args[0], 'https://api.github.com/graphql')
        sent_variables = graphql_call.kwargs['json']['variables']
        self.assertEqual(sent_variables['owner'], OWNER)
        self.assertEqual(sent_variables['name'], REPO)
        self.assertEqual(sent_variables['number'], 42)

        self.assertEqual(len(comments), 1)
        review = comments[0]
        self.assertIsInstance(review, ReviewComment)
        self.assertEqual(review.pull_request_id, '42')
        self.assertEqual(review.comment_id, '9001')
        self.assertEqual(review.author, 'reviewer')
        self.assertEqual(review.body, 'Cap the backoff so it cannot grow forever.')
        self.assertEqual(review.file_path, 'src/uploader.py')
        self.assertEqual(review.line_number, 27)
        self.assertEqual(review.line_type, 'added')
        self.assertEqual(review.commit_sha, 'abc123')
        self.assertEqual(
            getattr(review, ReviewCommentFields.RESOLUTION_TARGET_ID), 'thread-1'
        )

        # D — reply to the review comment over REST.
        with patch.object(client, '_post', return_value=mock_response()) as mock_reply:
            client.reply_to_review_comment(
                OWNER, REPO, review, 'Done — clamped the backoff at 30s.'
            )
        mock_reply.assert_called_once_with(
            f'/repos/{OWNER}/{REPO}/pulls/42/comments/9001/replies',
            json={'body': 'Done — clamped the backoff at 30s.'},
        )

        # E — resolve the thread via the GraphQL mutation.
        with patch.object(
            client.session, 'post', return_value=mock_response(json_data={'data': {}})
        ) as mock_resolve:
            client.resolve_review_comment(OWNER, REPO, review)
        self.assertEqual(
            mock_resolve.call_args.kwargs['json']['variables'],
            {'threadId': 'thread-1'},
        )


# ---------------------------------------------------------------------------
# F3 — GraphQL cursor pagination (threads beyond the first page)
# ---------------------------------------------------------------------------
class F3_ReviewCommentsPagination(unittest.TestCase):
    """Flow: review threads spanning two GraphQL pages are all collected."""

    def test_flow(self):
        client = _pr_client()

        page_one = mock_response(
            json_data=_review_threads_payload(
                [
                    {
                        'id': 'thread-a',
                        'isResolved': False,
                        'path': 'a.py',
                        'line': 1,
                        'comments': {
                            'nodes': [
                                {
                                    'databaseId': 1,
                                    'body': 'first',
                                    'author': {'login': 'reviewer'},
                                }
                            ]
                        },
                    }
                ],
                has_next_page=True,
                end_cursor='CURSOR-2',
            )
        )
        page_two = mock_response(
            json_data=_review_threads_payload(
                [
                    {
                        'id': 'thread-b',
                        'isResolved': False,
                        'path': 'b.py',
                        'line': 2,
                        'comments': {
                            'nodes': [
                                {
                                    'databaseId': 2,
                                    'body': 'second',
                                    'author': {'login': 'reviewer'},
                                }
                            ]
                        },
                    }
                ],
                has_next_page=False,
            )
        )

        with patch.object(
            client.session, 'post', side_effect=[page_one, page_two]
        ) as mock_graphql_post:
            comments = client.list_pull_request_comments(OWNER, REPO, '7')

        # Two pages fetched; the second carried the cursor from the first.
        self.assertEqual(mock_graphql_post.call_count, 2)
        self.assertIsNone(
            mock_graphql_post.call_args_list[0].kwargs['json']['variables']['cursor']
        )
        self.assertEqual(
            mock_graphql_post.call_args_list[1].kwargs['json']['variables']['cursor'],
            'CURSOR-2',
        )
        self.assertEqual([c.comment_id for c in comments], ['1', '2'])
        self.assertEqual([c.body for c in comments], ['first', 'second'])


# ---------------------------------------------------------------------------
# F4 — Empty backlog (edge case)
# ---------------------------------------------------------------------------
class F4_NoAssignedIssues(unittest.TestCase):
    """Flow: an empty issues response yields no records and no comment fetch."""

    def test_flow(self):
        client = _issues_client()
        empty = mock_response(json_data=[])

        with patch.object(client, '_get', return_value=empty) as mock_get:
            records = client.get_assigned_tasks(REPO, 'nobody', ['open'])

        self.assertEqual(records, [])
        # Only the list call — no per-issue comment round-trips.
        mock_get.assert_called_once()


# ---------------------------------------------------------------------------
# F5 — Transient timeout is retried (edge case)
# ---------------------------------------------------------------------------
class F5_CreatePullRequestRetriesOnTimeout(unittest.TestCase):
    """Flow: a single transient failure on PR creation is retried, then succeeds."""

    def test_flow(self):
        client = _pr_client()
        ok = mock_response(
            json_data={
                'number': 7,
                PullRequestFields.TITLE: 'Add retry to uploader',
                'html_url': f'https://github.com/{OWNER}/{REPO}/pull/7',
            }
        )

        with patch.object(
            client, '_post', side_effect=[ClientTimeout('connection reset'), ok]
        ) as mock_post:
            pr = client.create_pull_request(
                title='Add retry to uploader',
                source_branch='feature/101',
                repo_owner=OWNER,
                repo_slug=REPO,
                destination_branch='main',
            )

        self.assertEqual(mock_post.call_count, 2)
        self.assertEqual(pr[PullRequestFields.ID], '7')


# ---------------------------------------------------------------------------
# F6 — GraphQL null repository surfaces an error (edge case)
# ---------------------------------------------------------------------------
class F6_ReviewCommentsNullRepositoryRaises(unittest.TestCase):
    """Flow: a null ``repository`` (permission denied) raises, never silently empty."""

    def test_flow(self):
        client = _pr_client()
        null_repo = mock_response(json_data={'data': {'repository': None}})

        with patch.object(client.session, 'post', return_value=null_repo):
            with self.assertRaisesRegex(RuntimeError, 'null repository'):
                client.list_pull_request_comments(OWNER, REPO, '7')


if __name__ == '__main__':
    unittest.main()
