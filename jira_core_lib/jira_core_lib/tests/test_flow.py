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

from requests import HTTPError

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
from provider_client_base.provider_client_base.data.subtask import IssueComment, IssueStatus

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

        # --- D. post a comment back onto the issue (v3 takes it as ADF) ---
        comment_resp = mock_response()
        with patch.object(client, '_post', return_value=comment_resp) as mock_post:
            client.add_comment('DEMO-101', 'Opened a pull request with the retry.')
        mock_post.assert_called_once_with(
            '/rest/api/3/issue/DEMO-101/comment',
            json={'body': _adf_plain('Opened a pull request with the retry.')},
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


# ----- sub-task helpers -----

def _http_error(status_code: int, *, json_data=None, text: str = '') -> Mock:
    """A failed response whose ``raise_for_status`` raises as ``requests``
    does: an ``HTTPError`` carrying the response and so its status code."""
    response = mock_response(json_data=json_data, status_code=status_code, text=text)
    response.raise_for_status.side_effect = HTTPError(
        f'{status_code} Client Error', response=response,
    )
    return response


def _router(routes: dict):
    """A ``_get`` stand-in answering by path; a list answers in turn."""
    def fake_get(path, **kwargs):
        answer = routes[path]
        return answer.pop(0) if isinstance(answer, list) else answer
    return fake_get


def _user(account_id: str, display_name: str) -> dict:
    return {'accountId': account_id, 'displayName': display_name, 'active': True}


def _status_issue(key: str, status: str, *, assignee=None, parent: str = '') -> dict:
    return {'key': key, 'fields': {
        'status': {'name': status, 'statusCategory': {'key': 'indeterminate'}},
        'assignee': assignee,
        'created': '2026-10-17T13:00:00.000+0300',
        **({'parent': {'key': parent}} if parent else {}),
    }}


# ---------------------------------------------------------------------------
# F5 — Sub-task lifecycle: split an issue into work someone else may pick up
# ---------------------------------------------------------------------------
class F5_SubtaskLifecycle(unittest.TestCase):
    """The sub-task workflow, start to finish:

    who am I → create an UNASSIGNED sub-task (clearing the project's default
    assignee) → a retried create finds it by its marker → take it → read its
    state with its parent's in one search → post a progress record → rewrite
    that record in place → read the comments back, the record's lines
    unchanged.
    """

    def test_flow(self) -> None:
        client = _client()
        marker = 'subtask-ref: 7f3a'
        record = '{"step": "tests", "done": 2, "left": 1}\n\n  - retry: covered\n'

        # --- A. the account this client works as ---
        myself = mock_response(json_data=_user('acc-bot', 'Helper Bot'))
        with patch.object(client, '_get', return_value=myself) as mock_get:
            me = client.current_user()
        self.assertEqual(me, 'acc-bot')
        mock_get.assert_called_once_with('/rest/api/3/myself')

        # --- B. create the sub-task; the project's default assignee (its
        #        lead) is put on it anyway, so it is cleared ---
        routes = {
            '/rest/api/3/issue/DEMO-101': mock_response(json_data={'key': 'DEMO-101', 'fields': {
                'project': {'key': PROJECT, 'id': '10000'},
                'issuetype': {'id': '10001', 'name': 'Story', 'subtask': False},
            }}),
            f'/rest/api/3/project/{PROJECT}': mock_response(json_data={'key': PROJECT, 'issueTypes': [
                {'id': '10001', 'name': 'Story', 'subtask': False},
                {'id': '10003', 'name': 'Sub-task', 'subtask': True},
            ]}),
            '/rest/api/3/issue/DEMO-102': [
                mock_response(json_data={'fields': {'assignee': _user('acc-lead', 'Project Lead')}}),
                mock_response(json_data={'fields': {'assignee': None}}),
            ],
        }
        created = mock_response(json_data={'id': '10102', 'key': 'DEMO-102'})
        with patch.object(client, '_get', side_effect=_router(routes)) as mock_get, \
             patch.object(client, '_post', return_value=created) as mock_post, \
             patch.object(client, '_put', return_value=mock_response()) as mock_put:
            key = client.create_subtask(
                'DEMO-101', 'Cover the importer retry', f'Add tests for the retry.\n{marker}',
            )
        self.assertEqual(key, 'DEMO-102')
        self.assertEqual(mock_get.call_args_list, [
            call('/rest/api/3/issue/DEMO-101', params={'fields': 'project,issuetype'}),
            call(f'/rest/api/3/project/{PROJECT}'),
            call('/rest/api/3/issue/DEMO-102', params={'fields': 'assignee'}),
            call('/rest/api/3/issue/DEMO-102', params={'fields': 'assignee'}),
        ])
        sent = mock_post.call_args.kwargs['json']['fields']
        self.assertEqual(mock_post.call_args.args[0], '/rest/api/3/issue')
        self.assertEqual(sent['project'], {'key': PROJECT})
        self.assertEqual(sent['parent'], {'key': 'DEMO-101'})
        self.assertEqual(sent['issuetype'], {'id': '10003'})
        self.assertIsNone(sent['assignee'])
        self.assertEqual(sent['description'], _adf_doc(
            _adf_paragraph(_adf_text('Add tests for the retry.')),
            _adf_paragraph(_adf_text(marker)),
        ))
        mock_put.assert_called_once_with(
            '/rest/api/3/issue/DEMO-102/assignee', json={'accountId': None},
        )

        # --- C. a retried create finds the first one by its marker ---
        search = mock_response(json_data={'startAt': 0, 'maxResults': 100, 'total': 1, 'issues': [
            {'key': 'DEMO-102', 'fields': {'description': sent['description']}},
        ]})
        with patch.object(client, '_get', return_value=search) as mock_get:
            self.assertEqual(client.find_subtask('DEMO-101', marker), 'DEMO-102')
        self.assertEqual(mock_get.call_args.args[0], '/rest/api/3/search')
        self.assertEqual(
            mock_get.call_args.kwargs['params']['jql'], 'parent = "DEMO-101" ORDER BY created ASC',
        )

        # --- D. take it ---
        assigned = mock_response()
        with patch.object(client, '_put', return_value=assigned) as mock_put:
            client.assign_issue('DEMO-102', me)
        mock_put.assert_called_once_with(
            '/rest/api/3/issue/DEMO-102/assignee', json={'accountId': 'acc-bot'},
        )
        assigned.raise_for_status.assert_called_once_with()

        # --- E. its state and its parent's, in ONE search ---
        statuses_resp = mock_response(json_data={'startAt': 0, 'maxResults': 100, 'total': 2, 'issues': [
            _status_issue('DEMO-101', 'In Progress'),
            _status_issue('DEMO-102', 'In Progress', assignee=_user('acc-bot', 'Helper Bot'), parent='DEMO-101'),
        ]})
        with patch.object(client, '_get', return_value=statuses_resp) as mock_get:
            statuses = client.get_issues_status(['DEMO-101', 'DEMO-102'], 'status')
        mock_get.assert_called_once_with('/rest/api/3/search', params={
            'jql': 'key in ("DEMO-101", "DEMO-102")',
            'fields': 'status,assignee,created,parent',
            'startAt': 0,
            'maxResults': 100,
        })
        self.assertEqual(statuses['DEMO-102'], IssueStatus(
            issue_id='DEMO-102', state='In Progress', assignee='acc-bot',
            assignee_name='Helper Bot', created_at='2026-10-17T10:00:00+00:00',
            parent_id='DEMO-101',
        ))
        self.assertEqual(statuses['DEMO-101'].parent_id, '')

        # --- F. post a progress record: a line, then a code block ---
        posted = mock_response(json_data={'id': '10010'})
        with patch.object(client, '_post', return_value=posted) as mock_post:
            comment_id = client.add_preformatted_comment('DEMO-102', 'Progress:', record)
        self.assertEqual(comment_id, '10010')
        body = mock_post.call_args.kwargs['json']['body']
        self.assertEqual(mock_post.call_args.args[0], '/rest/api/3/issue/DEMO-102/comment')
        self.assertEqual(body, _adf_doc(
            _adf_paragraph(_adf_text('Progress:')),
            {'type': 'codeBlock', 'content': [_adf_text(record)]},
        ))

        # --- G. the record changes: rewritten in place, not posted again ---
        record = record.replace('"done": 2, "left": 1', '"done": 3, "left": 0')
        with patch.object(client, '_put', return_value=mock_response()) as mock_put:
            client.update_preformatted_comment('DEMO-102', comment_id, 'Progress:', record)
        rewritten = mock_put.call_args.kwargs['json']['body']
        self.assertEqual(mock_put.call_args.args[0], '/rest/api/3/issue/DEMO-102/comment/10010')
        self.assertEqual(rewritten['content'][1]['content'][0]['text'], record)

        # --- H. read the comments back: newest first, unfiltered, and the
        #        record's lines exactly as written ---
        comments_resp = mock_response(json_data={'startAt': 0, 'maxResults': 100, 'total': 2, 'comments': [
            {'id': '10011', 'author': _user('acc-alice', 'Alice'),
             'body': _adf_plain('@Bob can you look at this?'),
             'created': '2026-10-17T11:00:00.000+0000'},
            {'id': '10010', 'author': _user('acc-bot', 'Helper Bot'), 'body': rewritten,
             'created': '2026-10-17T13:30:00.000+0300'},
        ]})
        with patch.object(client, '_get', return_value=comments_resp) as mock_get:
            comments = client.list_comments('DEMO-102')
        mock_get.assert_called_once_with(
            '/rest/api/3/issue/DEMO-102/comment',
            params={'orderBy': '-created', 'startAt': 0, 'maxResults': 100},
        )
        self.assertEqual([c.comment_id for c in comments], ['10011', '10010'])
        self.assertEqual(comments[0].body, '@Bob can you look at this?')
        self.assertEqual(comments[1], IssueComment(
            comment_id='10010', author='acc-bot', author_name='Helper Bot',
            body='Progress:\n' + record, created_at='2026-10-17T10:30:00+00:00',
        ))
        intro, read_back = comments[1].body.split('\n', 1)
        self.assertEqual((intro, read_back), ('Progress:', record))


# ---------------------------------------------------------------------------
# F6 — Edge: one stale id among many does not hide the rest
# ---------------------------------------------------------------------------
class F6_StaleIdInBatchStatus(unittest.TestCase):
    """Jira rejects a whole ``key in (...)`` search with a 400 when one key
    is unknown. The read falls back to one GET per issue; the unknown one
    (a 404) reads as gone, every other one as it is."""

    def test_flow(self) -> None:
        client = _client()
        fields = 'status,assignee,created,parent'
        routes = {
            '/rest/api/3/search': _http_error(400, json_data={
                'errorMessages': ["An issue with key 'DEMO-404' does not exist for field 'key'."],
            }),
            '/rest/api/3/issue/DEMO-102': mock_response(
                json_data=_status_issue('DEMO-102', 'Done', parent='DEMO-101'),
            ),
            '/rest/api/3/issue/DEMO-404': _http_error(404, json_data={
                'errorMessages': ['Issue does not exist or you do not have permission to see it.'],
            }),
        }

        with patch.object(client, '_get', side_effect=_router(routes)) as mock_get:
            statuses = client.get_issues_status(['DEMO-102', 'DEMO-404'], 'Status')

        self.assertEqual(statuses, {
            'DEMO-102': IssueStatus(
                issue_id='DEMO-102', state='Done', created_at='2026-10-17T10:00:00+00:00',
                parent_id='DEMO-101',
            ),
            'DEMO-404': IssueStatus(issue_id='DEMO-404', exists=False),
        })
        self.assertEqual(mock_get.call_args_list[1:], [
            call('/rest/api/3/issue/DEMO-102', params={'fields': fields}),
            call('/rest/api/3/issue/DEMO-404', params={'fields': fields}),
        ])


# ---------------------------------------------------------------------------
# F7 — Edge: a sub-task cannot be split again
# ---------------------------------------------------------------------------
class F7_SubtaskOfASubtaskRefused(unittest.TestCase):
    """Jira allows no sub-task of a sub-task: asking for one raises a clear
    error before anything is created."""

    def test_flow(self) -> None:
        client = _client()
        parent = mock_response(json_data={'key': 'DEMO-102', 'fields': {
            'project': {'key': PROJECT},
            'issuetype': {'id': '10003', 'name': 'Sub-task', 'subtask': True},
        }})

        with patch.object(client, '_get', return_value=parent) as mock_get, \
             patch.object(client, '_post') as mock_post, \
             patch.object(client, '_put') as mock_put:
            with self.assertRaisesRegex(ValueError, 'DEMO-102 is itself a Jira sub-task'):
                client.create_subtask('DEMO-102', 'Too deep', 'nested')

        mock_get.assert_called_once_with('/rest/api/3/issue/DEMO-102', params={'fields': 'project,issuetype'})
        mock_post.assert_not_called()
        mock_put.assert_not_called()


if __name__ == '__main__':
    unittest.main()
