"""End-to-end flow tests for JiraClient — A-Z scenarios.

Each test class represents one named flow and exercises the full call
chain from a public method call down through mocked HTTP responses back
to the structured ``IssueRecord``.  No internal methods are patched; only
the lowest-level ``_get`` / ``_post`` / ``_put`` verb methods on the
client (and ``session.get`` for attachment downloads) are intercepted so
the full JQL building, ADF flattening, retry, and assembly logic runs.

The suite is fully self-contained: it builds Atlassian Document Format
(ADF) payloads the way the Jira Cloud REST API returns them and defines
its own ``mock_response`` helper so the library can be tested as a
standalone open-source package with no peer test dependencies.
"""
from __future__ import annotations

import unittest
from unittest.mock import Mock, call, patch

from jira_core_lib.jira_core_lib.client.jira_client import (
    JiraClient,
    _COMMENT_SECTION_TITLE,
    _SCREENSHOT_SECTION_TITLE,
    _TEXT_ATTACHMENTS_SECTION_TITLE,
)
from jira_core_lib.jira_core_lib.data.fields import (
    ISSUE_ALL_COMMENTS,
    ISSUE_COMMENT_AUTHOR,
    ISSUE_COMMENT_BODY,
    JiraAttachmentFields,
    JiraCommentFields,
    JiraIssueFields,
    JiraTransitionFields,
)
from provider_client_base.provider_client_base.data.issue_record import IssueRecord

BASE_URL = 'https://example.atlassian.net'
TOKEN = 'example-token'
PROJECT = 'DEMO'
ASSIGNEE = 'developer'
STATES = ['To Do', 'In Progress']


def _client(**kwargs) -> JiraClient:
    return JiraClient(BASE_URL, TOKEN, max_retries=1, **kwargs)


def mock_response(*, json_data=None, status_code: int = 200, text: str = '',
                  content: bytes = b'') -> Mock:
    """A minimal stand-in for a ``requests.Response``.

    ``raise_for_status`` is a no-op Mock by default; tests that exercise
    the error path set ``.side_effect`` on it.
    """
    response = Mock(status_code=status_code)
    response.json.return_value = json_data
    response.text = text
    response.content = content
    return response


# ----- ADF (Atlassian Document Format) builders -----

def _adf_doc(*content: dict) -> dict:
    """Wrap one or more ADF block nodes in a top-level ``doc`` node."""
    return {'type': 'doc', 'version': 1, 'content': list(content)}


def _adf_paragraph(*inline: dict) -> dict:
    return {'type': 'paragraph', 'content': list(inline)}


def _adf_text(text: str) -> dict:
    return {'type': 'text', 'text': text}


def _adf_mention(account_id: str, display: str) -> dict:
    """An ADF ``mention`` node — how Jira Cloud encodes ``@`` tags.

    The plain-text flattening drops these entirely, so the @-mention
    filter walks the raw ADF to find both the accountId and display text.
    """
    return {'type': 'mention', 'attrs': {'id': account_id, 'text': display}}


def _adf_plain(text: str) -> dict:
    """An ADF document holding a single plain-text paragraph."""
    return _adf_doc(_adf_paragraph(_adf_text(text)))


def _comment(text_or_node, author: str = 'Reviewer') -> dict:
    body = text_or_node if isinstance(text_or_node, dict) else _adf_plain(text_or_node)
    return {
        JiraCommentFields.BODY: body,
        JiraCommentFields.AUTHOR: {JiraCommentFields.DISPLAY_NAME: author},
    }


def _issue(
    key: str = 'DEMO-1',
    summary: str = 'Add a retry to the importer',
    description=None,
    comments: list | None = None,
    attachments: list | None = None,
    labels: list | None = None,
) -> dict:
    return {
        JiraIssueFields.KEY: key,
        'fields': {
            JiraIssueFields.SUMMARY: summary,
            JiraIssueFields.DESCRIPTION: description,
            JiraIssueFields.COMMENT: {'comments': comments or []},
            JiraIssueFields.ATTACHMENT: attachments or [],
            JiraIssueFields.LABELS: labels or [],
        },
    }


# ---------------------------------------------------------------------------
# F1 — Full A-Z lifecycle against a single issue
# ---------------------------------------------------------------------------
class F1_FullIssueLifecycle(unittest.TestCase):
    """The primary end-to-end workflow, start to finish:

    construct/auth → validate connection → fetch assigned issues via JQL →
    read ADF comments + attachments into the record → post a comment →
    transition status → add a label → remove a label.

    Every HTTP verb is dispatched against the real client code; only the
    lowest-level ``_get`` / ``_post`` / ``_put`` and ``session.get`` are
    mocked.  Endpoints and payloads are asserted at each step.
    """

    def test_flow(self) -> None:
        # --- A. construct + auth (basic auth via configured email) ---
        client = JiraClient(
            BASE_URL,
            TOKEN,
            'developer@example.com',
            max_retries=1,
            is_operational_comment=lambda body: body.startswith('[automation]'),
        )
        self.assertIsNone(client.headers)
        self.assertEqual(client.auth, ('developer@example.com', TOKEN))

        # --- B. validate_connection issues one search GET (maxResults=1) ---
        validate_resp = mock_response(json_data={'issues': []})
        with patch.object(client, '_get', return_value=validate_resp) as mock_get:
            client.validate_connection(PROJECT, ASSIGNEE, STATES)
        validate_resp.raise_for_status.assert_called_once_with()
        validate_params = mock_get.call_args.kwargs['params']
        self.assertEqual(mock_get.call_args.args[0], '/rest/api/3/search')
        self.assertEqual(validate_params['maxResults'], 1)
        self.assertEqual(validate_params['fields'], JiraIssueFields.KEY)
        self.assertIn(f'project = "{PROJECT}"', validate_params['jql'])
        self.assertIn(f'assignee = "{ASSIGNEE}"', validate_params['jql'])
        self.assertIn('"To Do", "In Progress"', validate_params['jql'])
        self.assertIn('ORDER BY updated DESC', validate_params['jql'])

        # --- C. fetch assigned issues; ADF description + comments +
        #        attachments are parsed into one IssueRecord ---
        issue = _issue(
            key='DEMO-101',
            summary='Add a retry to the importer',
            description=_adf_doc(
                _adf_paragraph(_adf_text('Importer fails on flaky network.')),
                _adf_paragraph(_adf_text('Add a bounded retry.')),
            ),
            comments=[
                _comment('[automation] nightly scan started', 'pipeline'),
                _comment('Please cap retries at three.', 'alice'),
            ],
            attachments=[
                {
                    JiraAttachmentFields.FILENAME: 'trace.txt',
                    JiraAttachmentFields.MIME_TYPE: 'text/plain',
                    JiraAttachmentFields.CONTENT: f'{BASE_URL}/attachment/trace.txt',
                },
                {
                    JiraAttachmentFields.FILENAME: 'failure.png',
                    JiraAttachmentFields.MIME_TYPE: 'image/png',
                    JiraAttachmentFields.CONTENT: f'{BASE_URL}/attachment/failure.png',
                    JiraAttachmentFields.SIZE: 20480,
                },
            ],
            labels=['importer', 'reliability'],
        )
        search_resp = mock_response(json_data={'issues': [issue]})
        attachment_resp = mock_response(text='RetryError: connection reset')

        with patch.object(client, '_get', return_value=search_resp) as mock_get, \
             patch.object(client.session, 'get', return_value=attachment_resp):
            records = client.get_assigned_tasks(PROJECT, ASSIGNEE, STATES)

        # endpoint + the full field projection were sent
        self.assertEqual(mock_get.call_args.args[0], '/rest/api/3/search')
        search_params = mock_get.call_args.kwargs['params']
        self.assertEqual(search_params['maxResults'], 100)
        self.assertEqual(
            search_params['fields'],
            'summary,description,comment,attachment,labels',
        )

        # response parsed into the lib's data type
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertIsInstance(record, IssueRecord)
        self.assertEqual(record.id, 'DEMO-101')
        self.assertEqual(record.summary, 'Add a retry to the importer')
        self.assertEqual(record.branch_name, 'feature/demo-101')
        self.assertEqual(record.tags, ['importer', 'reliability'])

        # ADF description flattened; both paragraphs present
        self.assertIn('Importer fails on flaky network.', record.description)
        self.assertIn('Add a bounded retry.', record.description)

        # operational comment dropped from description, human comment kept
        self.assertNotIn('[automation] nightly scan started', record.description)
        self.assertIn(_COMMENT_SECTION_TITLE, record.description)
        self.assertIn('alice: Please cap retries at three.', record.description)

        # both attachment kinds surfaced in their guarded sections
        self.assertIn(_TEXT_ATTACHMENTS_SECTION_TITLE, record.description)
        self.assertIn('RetryError: connection reset', record.description)
        self.assertIn(_SCREENSHOT_SECTION_TITLE, record.description)
        self.assertIn('failure.png', record.description)
        self.assertIn('20480 bytes', record.description)

        # all comments (including operational) preserved on the record
        all_comments = getattr(record, ISSUE_ALL_COMMENTS)
        self.assertEqual(len(all_comments), 2)
        self.assertEqual(all_comments[0][ISSUE_COMMENT_AUTHOR], 'pipeline')
        self.assertEqual(all_comments[1][ISSUE_COMMENT_BODY], 'Please cap retries at three.')

        # --- D. post a comment back onto the issue ---
        comment_resp = mock_response()
        with patch.object(client, '_post', return_value=comment_resp) as mock_post:
            client.add_comment('DEMO-101', 'Opened a pull request with the retry.')
        mock_post.assert_called_once_with(
            '/rest/api/3/issue/DEMO-101/comment',
            json={'body': 'Opened a pull request with the retry.'},
        )
        comment_resp.raise_for_status.assert_called_once_with()

        # --- E. transition status (status field → transitions endpoint) ---
        transitions_resp = mock_response(json_data={
            'transitions': [
                {
                    JiraTransitionFields.ID: '31',
                    JiraTransitionFields.NAME: 'Start Review',
                    JiraTransitionFields.TO: {JiraTransitionFields.NAME: 'In Review'},
                },
                {
                    JiraTransitionFields.ID: '41',
                    JiraTransitionFields.NAME: 'Done',
                    JiraTransitionFields.TO: {JiraTransitionFields.NAME: 'Done'},
                },
            ],
        })
        transition_post_resp = mock_response()
        with patch.object(client, '_get', return_value=transitions_resp) as mock_get, \
             patch.object(client, '_post', return_value=transition_post_resp) as mock_post:
            client.move_issue_to_state('DEMO-101', 'status', 'In Review')
        mock_get.assert_called_once_with('/rest/api/3/issue/DEMO-101/transitions')
        mock_post.assert_called_once_with(
            '/rest/api/3/issue/DEMO-101/transitions',
            json={'transition': {JiraTransitionFields.ID: '31'}},
        )

        # --- F. add then remove a label ---
        label_add_resp = mock_response()
        label_remove_resp = mock_response()
        with patch.object(
            client, '_put', side_effect=[label_add_resp, label_remove_resp],
        ) as mock_put:
            client.add_tag('DEMO-101', 'in-review')
            client.remove_tag('DEMO-101', 'in-review')
        self.assertEqual(mock_put.call_count, 2)
        self.assertEqual(
            mock_put.call_args_list[0],
            call(
                '/rest/api/3/issue/DEMO-101',
                json={'update': {'labels': [{'add': 'in-review'}]}},
            ),
        )
        self.assertEqual(
            mock_put.call_args_list[1],
            call(
                '/rest/api/3/issue/DEMO-101',
                json={'update': {'labels': [{'remove': 'in-review'}]}},
            ),
        )


# ---------------------------------------------------------------------------
# F2 — Edge: ADF mention node addressed to a human is dropped
# ---------------------------------------------------------------------------
class F2_AdfMentionAddressedToHumanDropped(unittest.TestCase):
    """A Cloud ``@mention`` is an ADF node, not ``@text``.

    A comment tagging another human must not reach the agent, while a
    comment tagging the bot (or with no mention) is kept.  The bot's
    accountId is resolved from ``/rest/api/3/myself`` only when no explicit
    login was configured.
    """

    def test_flow(self) -> None:
        client = _client()

        addressed_to_human = _comment(
            _adf_doc(_adf_paragraph(
                _adf_mention('account-alice', '@Alice'),
                _adf_text(' can you take this one?'),
            )),
            author='operator',
        )
        addressed_to_bot = _comment(
            _adf_doc(_adf_paragraph(
                _adf_mention('account-bot', '@Helper Bot'),
                _adf_text(' please add the retry.'),
            )),
            author='operator',
        )
        no_mention = _comment('Remember to bump the version.', 'operator')

        issue = _issue(
            key='DEMO-7',
            description=_adf_plain('Mention handling.'),
            comments=[addressed_to_human, addressed_to_bot, no_mention],
        )
        search_resp = mock_response(json_data={'issues': [issue]})
        myself_resp = mock_response(json_data={'accountId': 'account-bot'})

        def get_side_effect(path, **kwargs):
            if path == '/rest/api/3/myself':
                return myself_resp
            return search_resp

        with patch.object(client, '_get', side_effect=get_side_effect):
            records = client.get_assigned_tasks(PROJECT, ASSIGNEE, STATES)

        description = records[0].description
        # human-addressed comment dropped from the agent-facing description
        self.assertNotIn('can you take this one?', description)
        # bot-addressed + un-addressed comments survive
        self.assertIn('please add the retry.', description)
        self.assertIn('Remember to bump the version.', description)


# ---------------------------------------------------------------------------
# F3 — Edge: empty backlog returns an empty list
# ---------------------------------------------------------------------------
class F3_EmptyBacklog(unittest.TestCase):
    """No matching issues → an empty list, no parsing errors."""

    def test_flow(self) -> None:
        client = _client()
        empty = mock_response(json_data={'issues': []})

        with patch.object(client, '_get', return_value=empty):
            records = client.get_assigned_tasks(PROJECT, ASSIGNEE, STATES)

        self.assertEqual(records, [])


# ---------------------------------------------------------------------------
# F4 — Edge: requested status transition does not exist
# ---------------------------------------------------------------------------
class F4_TransitionNotFound(unittest.TestCase):
    """A status with no matching transition raises a clear ValueError and
    never POSTs a bogus transition."""

    def test_flow(self) -> None:
        client = _client()
        transitions_resp = mock_response(json_data={
            'transitions': [
                {
                    JiraTransitionFields.ID: '10',
                    JiraTransitionFields.NAME: 'Done',
                    JiraTransitionFields.TO: {JiraTransitionFields.NAME: 'Done'},
                },
            ],
        })

        with patch.object(client, '_get', return_value=transitions_resp), \
             patch.object(client, '_post') as mock_post:
            with self.assertRaisesRegex(ValueError, 'unknown jira transition: In Review'):
                client.move_issue_to_state('DEMO-1', 'status', 'In Review')

        mock_post.assert_not_called()


if __name__ == '__main__':
    unittest.main()
