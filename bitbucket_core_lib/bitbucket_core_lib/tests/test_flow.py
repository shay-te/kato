"""End-to-end flow tests for the Bitbucket core lib — A-Z scenarios.

Each test class drives one named flow through the full call chain: from a
public method on ``BitbucketIssuesClient`` / ``BitbucketClient`` down to the
mocked HTTP layer and back into the lib's structured data types
(``IssueRecord`` / ``ReviewComment``).

No internal helpers are patched — only the lowest-level ``_get`` / ``_post`` /
``_put`` / ``_delete`` on the client (the methods that call ``session.*``) are
intercepted, so the real auth, parsing, filtering, and assembly logic runs.

This module is intentionally self-contained: it does NOT import the top-level
``tests`` package (that one is product-coupled). A tiny local ``mock_response``
and a basic-auth helper are defined inline so the lib's tests stand alone as a
standalone open-source library.
"""
from __future__ import annotations

import unittest
from base64 import b64encode
from unittest.mock import Mock, patch

from bitbucket_core_lib.bitbucket_core_lib.client.bitbucket_client import (
    BITBUCKET_PAGE_LENGTH,
    BitbucketClient,
)
from bitbucket_core_lib.bitbucket_core_lib.client.bitbucket_issues_client import (
    BitbucketIssuesClient,
)
from bitbucket_core_lib.bitbucket_core_lib.data.fields import (
    ISSUE_ALL_COMMENTS,
    ISSUE_COMMENT_AUTHOR,
    ISSUE_COMMENT_BODY,
    BitbucketIssueFields,
)
from provider_client_base.provider_client_base.data.fields import (
    PullRequestFields,
    ReviewCommentFields,
)
from provider_client_base.provider_client_base.data.issue_record import IssueRecord
from provider_client_base.provider_client_base.data.review_comment import ReviewComment

BASE_URL = 'https://api.bitbucket.org/2.0'
TOKEN = 'bb-token'
WORKSPACE = 'acme'
REPO_SLUG = 'widget'
USERNAME = 'review_bot'
ASSIGNEE = 'maria'


def mock_response(*, json_data=None, status_code: int = 200, text='', ok=True):
    """Minimal stand-in for a ``requests.Response`` (self-contained)."""
    response = Mock(status_code=status_code)
    response.json.return_value = json_data
    response.text = text
    response.ok = ok
    return response


def _basic_auth_header(username: str, token: str) -> str:
    encoded = b64encode(f'{username}:{token}'.encode('utf-8')).decode('ascii')
    return f'Basic {encoded}'


def _issues_client(**kwargs) -> BitbucketIssuesClient:
    return BitbucketIssuesClient(
        BASE_URL, TOKEN, WORKSPACE, REPO_SLUG, max_retries=1, **kwargs
    )


def _pull_request_client(**kwargs) -> BitbucketClient:
    return BitbucketClient(BASE_URL, TOKEN, max_retries=1, **kwargs)


# ---------------------------------------------------------------------------
# F1 — Issue lifecycle A->E: auth -> validate -> list -> comment -> move/tag
# ---------------------------------------------------------------------------
class F1_IssueLifecycle(unittest.TestCase):
    """A->E: construct with auth, validate the repo, pull assigned issues
    (with comments + tags), post a working note, advance state, set a tag."""

    def test_flow(self) -> None:
        operational_prefixes = ('Review agent', 'Bot:')
        client = _issues_client(
            username=USERNAME,
            is_operational_comment=lambda text: any(
                text.startswith(p) for p in operational_prefixes
            ),
        )

        # A) auth header is built from the configured username + token.
        self.assertEqual(
            client.headers['Authorization'],
            _basic_auth_header(USERNAME, TOKEN),
        )

        # B) validate_connection -> single GET against the issues endpoint.
        validate_resp = mock_response(json_data={'values': []})

        # C) get_assigned_tasks -> issues list, then per-issue comments.
        issues_resp = mock_response(json_data={
            'values': [
                {
                    'id': 42,
                    'title': 'Add a logout button',
                    'content': {'raw': 'Users want to sign out from the header.'},
                    'state': 'new',
                    'assignee': {'nickname': ASSIGNEE},
                    BitbucketIssueFields.LABELS: ['frontend', 'priority:high'],
                },
                {
                    'id': 7,
                    'title': 'Unrelated done task',
                    'content': {'raw': 'nope'},
                    'state': 'resolved',
                    'assignee': {'nickname': ASSIGNEE},
                },
            ]
        })
        comments_resp = mock_response(json_data={
            'values': [
                {'content': {'raw': 'Review agent started working'},
                 'user': {'display_name': 'review_bot'}},
                {'content': {'raw': 'Please match the existing styles.'},
                 'user': {'display_name': 'maria'}},
            ]
        })

        # get order: validate, issues list, comments for the one matching issue.
        get_calls = [validate_resp, issues_resp, comments_resp]
        with patch.object(client, '_get', side_effect=get_calls):
            client.validate_connection(REPO_SLUG, ASSIGNEE, ['new'])
            tasks = client.get_assigned_tasks(REPO_SLUG, ASSIGNEE, ['new'])

        validate_resp.raise_for_status.assert_called_once()

        # Only the 'new' issue assigned to maria survives state filtering.
        self.assertEqual(len(tasks), 1)
        task = tasks[0]
        self.assertIsInstance(task, IssueRecord)
        self.assertEqual(task.id, '42')
        self.assertEqual(task.summary, 'Add a logout button')
        self.assertEqual(task.tags, ['frontend', 'priority:high'])
        self.assertEqual(task.branch_name, 'feature/42')
        # Real reviewer comment is folded into the description; the
        # operational bot note is excluded from it (kept in all_comments).
        self.assertIn('maria: Please match the existing styles.', task.description)
        self.assertNotIn('Review agent started working', task.description)
        all_comments = getattr(task, ISSUE_ALL_COMMENTS)
        self.assertEqual(len(all_comments), 2)
        bodies = [entry[ISSUE_COMMENT_BODY] for entry in all_comments]
        self.assertIn('Review agent started working', bodies)

        # D) add_comment -> POST raw content payload to the issue.
        comment_resp = mock_response()
        with patch.object(client, '_post', return_value=comment_resp) as mock_post:
            client.add_comment('42', 'On it.')
        comment_resp.raise_for_status.assert_called_once()
        mock_post.assert_called_once_with(
            '/repositories/acme/widget/issues/42/comments',
            json={'content': {'raw': 'On it.'}},
        )

        # E) move_issue_to_state -> PUT the new state value.
        move_resp = mock_response()
        with patch.object(client, '_put', return_value=move_resp) as mock_put:
            client.move_issue_to_state('42', 'state', 'on hold')
        mock_put.assert_called_once_with(
            '/repositories/acme/widget/issues/42',
            json={'state': 'on hold'},
        )

    def test_tag_add_then_remove_round_trip(self) -> None:
        """E (cont.): set a tag, then clear it once it matches the component."""
        client = _issues_client()

        add_resp = mock_response()
        with patch.object(client, '_put', return_value=add_resp) as mock_put:
            client.add_tag('42', 'bot:triage:investigate')
        mock_put.assert_called_once_with(
            '/repositories/acme/widget/issues/42',
            json={'component': {'name': 'bot:triage:investigate'}},
        )

        # remove_tag reads the issue first, sees the matching component, clears it.
        get_resp = mock_response(json_data={'component': {'name': 'bot:triage:investigate'}})
        clear_resp = mock_response()
        with patch.object(client, '_get', return_value=get_resp), \
             patch.object(client, '_put', return_value=clear_resp) as mock_clear:
            client.remove_tag('42', 'bot:triage:investigate')
        mock_clear.assert_called_once_with(
            '/repositories/acme/widget/issues/42',
            json={'component': None},
        )


# ---------------------------------------------------------------------------
# F2 — Empty issue list
# ---------------------------------------------------------------------------
class F2_NoAssignedIssues(unittest.TestCase):
    """Flow: nothing matching the assignee/state returns an empty list."""

    def test_flow(self) -> None:
        client = _issues_client()
        empty = mock_response(json_data={'values': []})

        with patch.object(client, '_get', return_value=empty):
            tasks = client.get_assigned_tasks(REPO_SLUG, ASSIGNEE, ['new'])

        self.assertEqual(tasks, [])


# ---------------------------------------------------------------------------
# F3 — Edge: comment @-addressed to another human is dropped
# ---------------------------------------------------------------------------
class F3_MentionAddressedToHumanDropped(unittest.TestCase):
    """Edge: a comment mentioning another person is not folded into the task,
    but a comment mentioning the bot (or no one) is kept."""

    def test_flow(self) -> None:
        client = _issues_client(bot_login='review_bot')

        issues_resp = mock_response(json_data={
            'values': [{
                'id': 100,
                'title': 'Tweak the footer',
                'content': {'raw': 'The footer needs spacing.'},
                'state': 'new',
                'assignee': {'nickname': ASSIGNEE},
            }]
        })
        comments_resp = mock_response(json_data={
            'values': [
                {'content': {'raw': '@maria can you take this one?'},
                 'user': {'display_name': 'lead'}},          # dropped (other human)
                {'content': {'raw': 'Add a unit test for it.'},
                 'user': {'display_name': 'lead'}},          # kept (no mention)
                {'content': {'raw': '@review_bot fix the typo'},
                 'user': {'display_name': 'lead'}},          # kept (mentions bot)
            ]
        })

        with patch.object(client, '_get', side_effect=[issues_resp, comments_resp]):
            tasks = client.get_assigned_tasks(REPO_SLUG, ASSIGNEE, ['new'])

        all_comments = getattr(tasks[0], ISSUE_ALL_COMMENTS)
        bodies = [entry[ISSUE_COMMENT_BODY] for entry in all_comments]
        self.assertIn('Add a unit test for it.', bodies)
        self.assertIn('@review_bot fix the typo', bodies)
        self.assertNotIn('@maria can you take this one?', bodies)

    def test_brace_account_id_mention_for_human_dropped(self) -> None:
        """Bitbucket Cloud encodes mentions as ``@{account_id}`` — a tag of
        another account is resolved against the bot's id and dropped."""
        client = _issues_client()  # no explicit bot_login -> resolve from API

        issues_resp = mock_response(json_data={
            'values': [{
                'id': 101,
                'title': 'Rename a field',
                'content': {'raw': 'Rename ``foo`` to ``bar``.'},
                'state': 'new',
                'assignee': {'nickname': ASSIGNEE},
            }]
        })
        comments_resp = mock_response(json_data={
            'values': [
                {'content': {'raw': '@{other-account} please look'},
                 'user': {'display_name': 'lead'}},          # dropped
                {'content': {'raw': '@{bot-account} go ahead'},
                 'user': {'display_name': 'lead'}},          # kept
            ]
        })

        with patch.object(client, '_get', side_effect=[issues_resp, comments_resp]), \
             patch.object(
                 client, '_fetch_current_user_logins', return_value=('bot-account',),
             ):
            tasks = client.get_assigned_tasks(REPO_SLUG, ASSIGNEE, ['new'])

        all_comments = getattr(tasks[0], ISSUE_ALL_COMMENTS)
        bodies = [entry[ISSUE_COMMENT_BODY] for entry in all_comments]
        self.assertEqual(bodies, ['@{bot-account} go ahead'])


# ---------------------------------------------------------------------------
# F4 — Edge: operational/bot comment excluded from description, kept in all_comments
# ---------------------------------------------------------------------------
class F4_OperationalCommentFiltering(unittest.TestCase):
    """Edge: a bot-posted operational comment is excluded from the agent-facing
    description but still retained in ``all_comments`` for the record."""

    def test_flow(self) -> None:
        client = _issues_client(
            is_operational_comment=lambda text: text.startswith('Review agent'),
        )

        issues_resp = mock_response(json_data={
            'values': [{
                'id': 5,
                'title': 'Improve error handling',
                'content': {'raw': 'Wrap the call in a try/except.'},
                'state': 'new',
                'assignee': {'nickname': ASSIGNEE},
            }]
        })
        comments_resp = mock_response(json_data={
            'values': [
                {'content': {'raw': 'Review agent could not finish: timeout'},
                 'user': {'display_name': 'review_bot'}},
                {'content': {'raw': 'Cover the empty-input case too.'},
                 'user': {'display_name': 'maria'}},
            ]
        })

        with patch.object(client, '_get', side_effect=[issues_resp, comments_resp]):
            tasks = client.get_assigned_tasks(REPO_SLUG, ASSIGNEE, ['new'])

        task = tasks[0]
        self.assertIn('maria: Cover the empty-input case too.', task.description)
        self.assertNotIn('could not finish', task.description)
        # Operational comment is preserved in the full record.
        all_comments = getattr(task, ISSUE_ALL_COMMENTS)
        self.assertEqual(len(all_comments), 2)
        bodies = [entry[ISSUE_COMMENT_BODY] for entry in all_comments]
        self.assertIn('Review agent could not finish: timeout', bodies)
        authors = [entry[ISSUE_COMMENT_AUTHOR] for entry in all_comments]
        self.assertIn('maria', authors)


# ---------------------------------------------------------------------------
# F5 — Best-effort: comments fetch failure does not fail the task
# ---------------------------------------------------------------------------
class F5_CommentsFetchFailsGracefully(unittest.TestCase):
    """Flow: if the comments endpoint errors, the task still returns (empty
    comments) rather than the whole scan blowing up."""

    def test_flow(self) -> None:
        client = _issues_client()
        issues_resp = mock_response(json_data={
            'values': [{
                'id': 9,
                'title': 'Cache the result',
                'content': {'raw': 'Memoize the expensive lookup.'},
                'state': 'new',
                'assignee': {'nickname': ASSIGNEE},
            }]
        })

        def side_effect(path, **_kwargs):
            if 'comments' in path:
                raise RuntimeError('comments endpoint down')
            return issues_resp

        with patch.object(client, '_get', side_effect=side_effect):
            tasks = client.get_assigned_tasks(REPO_SLUG, ASSIGNEE, ['new'])

        self.assertEqual(len(tasks), 1)
        self.assertEqual(getattr(tasks[0], ISSUE_ALL_COMMENTS), [])


# ---------------------------------------------------------------------------
# F6 — Pull-request lifecycle F->K: create -> list -> reply -> resolve -> find
# ---------------------------------------------------------------------------
class F6_PullRequestLifecycle(unittest.TestCase):
    """F->K: open a PR, read its review comments, reply to one, resolve a
    thread, then locate the open PR again by branch + title."""

    def test_flow(self) -> None:
        client = _pull_request_client(username=USERNAME)

        # F) create_pull_request -> POST the PR payload, parse the response.
        create_resp = mock_response(json_data={
            PullRequestFields.ID: 31,
            PullRequestFields.TITLE: 'Add a logout button',
            'links': {'html': {'href': 'https://bitbucket.org/acme/widget/pull-requests/31'}},
        })
        with patch.object(client, '_post', return_value=create_resp) as mock_post:
            pull_request = client.create_pull_request(
                title='Add a logout button',
                source_branch='feature/42',
                repo_owner=WORKSPACE,
                repo_slug=REPO_SLUG,
                destination_branch='main',
                description='Closes the logout gap.',
            )

        create_resp.raise_for_status.assert_called_once()
        mock_post.assert_called_once_with(
            '/repositories/acme/widget/pullrequests',
            json={
                PullRequestFields.TITLE: 'Add a logout button',
                PullRequestFields.DESCRIPTION: 'Closes the logout gap.',
                'source': {'branch': {'name': 'feature/42'}},
                'destination': {'branch': {'name': 'main'}},
            },
        )
        self.assertEqual(pull_request, {
            PullRequestFields.ID: '31',
            PullRequestFields.TITLE: 'Add a logout button',
            PullRequestFields.URL: 'https://bitbucket.org/acme/widget/pull-requests/31',
        })
        pull_request_id = pull_request[PullRequestFields.ID]

        # G) list_pull_request_comments -> parse open inline comments into
        # ReviewComment objects (resolved + deleted threads are skipped).
        comments_resp = mock_response(json_data={
            'values': [
                {
                    'id': 200,
                    'content': {'raw': 'Rename this variable.'},
                    'user': {'display_name': 'maria'},
                    'inline': {'path': 'src/header.py', 'to': 12, 'from': None},
                    'commit': {'hash': 'abc123'},
                },
                {
                    'id': 201,
                    'resolution': {'type': 'resolved'},
                    'content': {'raw': 'Already handled.'},
                    'user': {'display_name': 'maria'},
                },
                {
                    'id': 202,
                    'deleted': True,
                    'content': {'raw': 'oops'},
                    'user': {'display_name': 'maria'},
                },
            ]
        })
        with patch.object(client, '_get', return_value=comments_resp) as mock_get:
            review_comments = client.list_pull_request_comments(
                WORKSPACE, REPO_SLUG, pull_request_id,
            )

        mock_get.assert_called_once_with(
            '/repositories/acme/widget/pullrequests/31/comments',
            params={'pagelen': BITBUCKET_PAGE_LENGTH, 'sort': 'created_on'},
        )
        self.assertEqual(len(review_comments), 1)
        review_comment = review_comments[0]
        self.assertIsInstance(review_comment, ReviewComment)
        self.assertEqual(review_comment.pull_request_id, '31')
        self.assertEqual(review_comment.comment_id, '200')
        self.assertEqual(review_comment.author, 'maria')
        self.assertEqual(review_comment.body, 'Rename this variable.')
        self.assertEqual(review_comment.file_path, 'src/header.py')
        self.assertEqual(review_comment.line_number, 12)
        self.assertEqual(review_comment.line_type, 'added')
        self.assertEqual(review_comment.commit_sha, 'abc123')
        self.assertEqual(
            getattr(review_comment, ReviewCommentFields.RESOLUTION_TARGET_ID),
            '200',
        )

        # H) reply_to_review_comment -> POST a threaded reply on the parent.
        reply_resp = mock_response(ok=True)
        with patch.object(client, '_post', return_value=reply_resp) as mock_reply:
            client.reply_to_review_comment(
                WORKSPACE, REPO_SLUG, review_comment,
                'Done — renamed it to ``logout_label``.',
            )
        mock_reply.assert_called_once_with(
            '/repositories/acme/widget/pullrequests/31/comments',
            json={
                'content': {'raw': 'Done — renamed it to ``logout_label``.'},
                'parent': {'id': 200},
            },
        )

        # I) resolve_review_comment -> POST to the thread's resolve endpoint.
        resolve_resp = mock_response()
        with patch.object(client, '_post', return_value=resolve_resp) as mock_resolve:
            client.resolve_review_comment(WORKSPACE, REPO_SLUG, review_comment)
        resolve_resp.raise_for_status.assert_called_once()
        mock_resolve.assert_called_once_with(
            '/repositories/acme/widget/pullrequests/31/comments/200/resolve',
        )

        # J/K) find_pull_requests -> filter the open PRs by branch + title.
        find_resp = mock_response(json_data={
            'values': [
                {
                    'id': 31,
                    PullRequestFields.TITLE: 'Add a logout button',
                    'links': {'html': {'href': 'https://bitbucket.org/acme/widget/pull-requests/31'}},
                    'source': {'branch': {'name': 'feature/42'}},
                },
                {
                    'id': 9,
                    PullRequestFields.TITLE: 'Something else',
                    'links': {'html': {'href': 'https://bitbucket.org/acme/widget/pull-requests/9'}},
                    'source': {'branch': {'name': 'feature/other'}},
                },
            ]
        })
        with patch.object(client, '_get', return_value=find_resp):
            found = client.find_pull_requests(
                WORKSPACE, REPO_SLUG,
                source_branch='feature/42',
                title_prefix='Add a logout',
            )

        self.assertEqual(found, [{
            PullRequestFields.ID: '31',
            PullRequestFields.TITLE: 'Add a logout button',
            PullRequestFields.URL: 'https://bitbucket.org/acme/widget/pull-requests/31',
        }])


# ---------------------------------------------------------------------------
# F7 — PR comment pagination follows the ``next`` link
# ---------------------------------------------------------------------------
class F7_PullRequestCommentsPaginate(unittest.TestCase):
    """Flow: list_pull_request_comments walks every page via ``next``."""

    def test_flow(self) -> None:
        client = _pull_request_client()
        page_one = mock_response(json_data={
            'values': [{
                'id': 1,
                'content': {'raw': 'first page note'},
                'user': {'display_name': 'maria'},
            }],
            'next': 'https://api.bitbucket.org/2.0/page2',
        })
        page_two = mock_response(json_data={
            'values': [{
                'id': 2,
                'content': {'raw': 'second page note'},
                'user': {'display_name': 'reviewer'},
            }],
        })

        with patch.object(client, '_get', side_effect=[page_one, page_two]) as mock_get:
            comments = client.list_pull_request_comments(WORKSPACE, REPO_SLUG, '31')

        self.assertEqual([c.comment_id for c in comments], ['1', '2'])
        self.assertEqual(mock_get.call_count, 2)
        # The follow-up page is fetched by its absolute ``next`` URL, no params.
        mock_get.assert_any_call('https://api.bitbucket.org/2.0/page2', params={})


# ---------------------------------------------------------------------------
# F8 — Create PR raises on a malformed response payload
# ---------------------------------------------------------------------------
class F8_CreatePullRequestInvalidPayload(unittest.TestCase):
    """Flow: a response missing the id field is rejected, not silently
    turned into a half-built PR record."""

    def test_flow(self) -> None:
        client = _pull_request_client()
        bad_resp = mock_response(json_data={PullRequestFields.TITLE: 'no id here'})

        with patch.object(client, '_post', return_value=bad_resp):
            with self.assertRaisesRegex(ValueError, 'invalid pull request response payload'):
                client.create_pull_request(
                    title='no id here',
                    source_branch='feature/42',
                    repo_owner=WORKSPACE,
                    repo_slug=REPO_SLUG,
                )


if __name__ == '__main__':
    unittest.main()
