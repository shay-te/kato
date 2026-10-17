import unittest
from unittest.mock import call, patch

from requests import HTTPError

from jira_core_lib.jira_core_lib.client.jira_client import (
    JiraClient,
    _COMMENT_SECTION_TITLE,
    _TEXT_ATTACHMENTS_SECTION_TITLE,
    _SCREENSHOT_SECTION_TITLE,
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
from provider_client_base.provider_client_base.testing import assert_client_headers_and_timeout, mock_response


def _make_client(**kwargs) -> JiraClient:
    return JiraClient('https://jira.example', 'jira-token', **kwargs)


def _http_error_response(status_code: int, *, json_data=None, text: str = ''):
    """A response whose ``raise_for_status`` raises the way ``requests`` does:
    an ``HTTPError`` carrying the response (and so its status code)."""
    response = mock_response(status_code=status_code, json_data=json_data, text=text)
    response.raise_for_status.side_effect = HTTPError(
        f'{status_code} Client Error', response=response,
    )
    return response


def _route_get(routes: dict):
    """A ``_get`` stand-in answering by path; a list answers in turn."""
    def fake_get(path, **kwargs):
        answer = routes[path]
        return answer.pop(0) if isinstance(answer, list) else answer
    return fake_get


def _adf_paragraph(text: str) -> dict:
    return {
        'type': 'doc',
        'content': [
            {'type': 'paragraph', 'content': [{'type': 'text', 'text': text}]}
        ],
    }


def _issue_payload(
    key: str = 'PROJ-1',
    summary: str = 'fix it already',
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


def _comment_payload(text: str, author: str = 'Reviewer') -> dict:
    return {
        JiraCommentFields.BODY: _adf_paragraph(text),
        JiraCommentFields.AUTHOR: {JiraCommentFields.DISPLAY_NAME: author},
    }


class JiraClientInitTests(unittest.TestCase):
    def test_uses_bearer_auth_by_default(self) -> None:
        client = _make_client(max_retries=5)

        self.assertEqual(client.max_retries, 5)
        assert_client_headers_and_timeout(self, client, 'jira-token', 30)

    def test_uses_basic_auth_when_email_is_configured(self) -> None:
        client = JiraClient('https://jira.example', 'jira-token', 'dev@example.com')

        self.assertIsNone(client.headers)
        self.assertEqual(client.auth, ('dev@example.com', 'jira-token'))

    def test_ignores_blank_email(self) -> None:
        client = JiraClient('https://jira.example', 'jira-token', '   ')

        assert_client_headers_and_timeout(self, client, 'jira-token', 30)

    def test_default_is_operational_comment_always_false(self) -> None:
        client = _make_client()
        self.assertFalse(client._is_operational_comment('anything'))

    def test_custom_is_operational_comment_injectable(self) -> None:
        client = _make_client(is_operational_comment=lambda body: body.startswith('[bot]'))
        self.assertTrue(client._is_operational_comment('[bot] scanning'))
        self.assertFalse(client._is_operational_comment('human'))


class JiraClientValidateConnectionTests(unittest.TestCase):
    def test_queries_search_api_with_correct_jql(self) -> None:
        client = _make_client()
        response = mock_response(json_data={'issues': []})

        with patch.object(client, '_get', return_value=response) as mock_get:
            client.validate_connection('PROJ', 'developer', ['To Do', 'Open'])

        mock_get.assert_called_once_with(
            '/rest/api/3/search',
            params={
                'jql': 'project = "PROJ" AND assignee = "developer" AND status IN ("To Do", "Open") ORDER BY updated DESC',
                'fields': 'key',
                'maxResults': 1,
            },
        )
        response.raise_for_status.assert_called_once_with()

    def test_raises_when_states_empty(self) -> None:
        client = _make_client()
        with self.assertRaisesRegex(ValueError, 'states must not be empty'):
            client.validate_connection('PROJ', 'developer', [])


class JiraClientGetAssignedTasksTests(unittest.TestCase):
    def test_returns_issue_records(self) -> None:
        client = _make_client()
        response = mock_response(
            json_data={'issues': [_issue_payload(description=_adf_paragraph('Details'))]}
        )

        with patch.object(client, '_get', return_value=response):
            records = client.get_assigned_tasks('PROJ', 'developer', ['To Do'])

        self.assertEqual(len(records), 1)
        self.assertIsInstance(records[0], IssueRecord)
        self.assertEqual(records[0].id, 'PROJ-1')
        self.assertIn('Details', records[0].description)

    def test_sends_correct_params(self) -> None:
        client = _make_client()
        response = mock_response(json_data={'issues': []})

        with patch.object(client, '_get', return_value=response) as mock_get:
            client.get_assigned_tasks('PROJ', 'developer', ['To Do'])

        mock_get.assert_called_once_with(
            '/rest/api/3/search',
            params={
                'jql': 'project = "PROJ" AND assignee = "developer" AND status IN ("To Do") ORDER BY updated DESC',
                'fields': 'summary,description,comment,attachment,labels',
                'maxResults': 100,
            },
        )

    def test_uses_labels_as_tags(self) -> None:
        client = _make_client()
        response = mock_response(
            json_data={'issues': [_issue_payload(labels=['repo:client', 'priority:high'])]}
        )

        with patch.object(client, '_get', return_value=response):
            records = client.get_assigned_tasks('PROJ', 'developer', ['To Do'])

        self.assertEqual(records[0].tags, ['repo:client', 'priority:high'])

    def test_maps_comments_into_description(self) -> None:
        client = _make_client()
        response = mock_response(
            json_data={
                'issues': [
                    _issue_payload(
                        description=_adf_paragraph('Main'),
                        comments=[_comment_payload('Please add tests.')],
                    )
                ]
            }
        )

        with patch.object(client, '_get', return_value=response):
            records = client.get_assigned_tasks('PROJ', 'developer', ['To Do'])

        self.assertIn(_COMMENT_SECTION_TITLE + ':', records[0].description)
        self.assertIn('Reviewer: Please add tests.', records[0].description)

    def test_maps_text_attachments_into_description(self) -> None:
        client = _make_client()
        attachment = {
            JiraAttachmentFields.FILENAME: 'notes.txt',
            JiraAttachmentFields.MIME_TYPE: 'text/plain',
            JiraAttachmentFields.CONTENT: 'https://jira.example/attachment/1',
        }
        response = mock_response(
            json_data={'issues': [_issue_payload(attachments=[attachment])]}
        )
        attachment_response = mock_response(text='Attachment body')

        with patch.object(client, '_get', return_value=response), \
             patch.object(client.session, 'get', return_value=attachment_response):
            records = client.get_assigned_tasks('PROJ', 'developer', ['To Do'])

        self.assertIn(_TEXT_ATTACHMENTS_SECTION_TITLE + ':', records[0].description)
        self.assertIn('Attachment notes.txt:\nAttachment body', records[0].description)

    def test_maps_screenshot_attachments_into_description(self) -> None:
        client = _make_client()
        attachment = {
            JiraAttachmentFields.FILENAME: 'screen.png',
            JiraAttachmentFields.MIME_TYPE: 'image/png',
            JiraAttachmentFields.CONTENT: 'https://jira.example/attachment/2',
            JiraAttachmentFields.SIZE: 12345,
        }
        response = mock_response(
            json_data={'issues': [_issue_payload(attachments=[attachment])]}
        )

        with patch.object(client, '_get', return_value=response):
            records = client.get_assigned_tasks('PROJ', 'developer', ['To Do'])

        self.assertIn(_SCREENSHOT_SECTION_TITLE + ':', records[0].description)
        self.assertIn('screen.png', records[0].description)
        self.assertIn('12345 bytes', records[0].description)

    def test_operational_comments_excluded_from_description_but_in_all_comments(self) -> None:
        client = _make_client(
            is_operational_comment=lambda body: 'agent could not safely process' in body
        )
        response = mock_response(
            json_data={
                'issues': [
                    _issue_payload(
                        description=_adf_paragraph('Details'),
                        comments=[
                            _comment_payload('agent could not safely process this task: timeout', 'shay'),
                            _comment_payload('Please add tests.', 'Reviewer'),
                        ],
                    )
                ]
            }
        )

        with patch.object(client, '_get', return_value=response):
            records = client.get_assigned_tasks('PROJ', 'developer', ['To Do'])

        self.assertIn('Reviewer: Please add tests.', records[0].description)
        self.assertNotIn('could not safely process', records[0].description)
        all_comments = getattr(records[0], ISSUE_ALL_COMMENTS)
        self.assertEqual(len(all_comments), 2)
        self.assertEqual(all_comments[0][ISSUE_COMMENT_AUTHOR], 'shay')

    def test_handles_non_dict_items_gracefully(self) -> None:
        client = _make_client()
        response = mock_response(
            json_data={'issues': ['not-a-dict', None, _issue_payload()]}
        )

        with patch.object(client, '_get', return_value=response):
            records = client.get_assigned_tasks('PROJ', 'developer', ['To Do'])

        self.assertEqual(len(records), 1)

    def test_skips_malformed_issues_that_raise(self) -> None:
        malformed = {'fields': {}}  # missing KEY
        good = _issue_payload(key='PROJ-5')
        response = mock_response(json_data={'issues': [malformed, good]})
        client = _make_client()

        with patch.object(client, '_get', return_value=response):
            records = client.get_assigned_tasks('PROJ', 'developer', ['To Do'])

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].id, 'PROJ-5')

    def test_raises_when_states_empty(self) -> None:
        client = _make_client()
        with self.assertRaisesRegex(ValueError, 'states must not be empty'):
            client.get_assigned_tasks('PROJ', 'developer', [])

    def test_returns_empty_list_when_no_issues(self) -> None:
        client = _make_client()
        response = mock_response(json_data={'issues': []})

        with patch.object(client, '_get', return_value=response):
            records = client.get_assigned_tasks('PROJ', 'developer', ['To Do'])

        self.assertEqual(records, [])

    def test_falls_back_no_description_provided(self) -> None:
        client = _make_client()
        response = mock_response(
            json_data={'issues': [_issue_payload(description=None)]}
        )

        with patch.object(client, '_get', return_value=response):
            records = client.get_assigned_tasks('PROJ', 'developer', ['To Do'])

        self.assertIn('No description provided.', records[0].description)


class JiraClientAddCommentTests(unittest.TestCase):
    def test_posts_the_body_as_an_adf_document(self) -> None:
        # v3 rejects a plain-string body with a 400; it takes ADF.
        client = _make_client()
        response = mock_response()

        with patch.object(client, '_post', return_value=response) as mock_post:
            client.add_comment('PROJ-1', 'Ready for review')

        mock_post.assert_called_once_with(
            '/rest/api/3/issue/PROJ-1/comment',
            json={'body': {
                'type': 'doc',
                'version': 1,
                'content': [
                    {'type': 'paragraph', 'content': [{'type': 'text', 'text': 'Ready for review'}]},
                ],
            }},
        )
        response.raise_for_status.assert_called_once_with()

    def test_each_line_becomes_a_paragraph(self) -> None:
        client = _make_client()

        with patch.object(client, '_post', return_value=mock_response()) as mock_post:
            client.add_comment('PROJ-1', 'Opened a pull request.\n\nIt adds a retry.')

        body = mock_post.call_args.kwargs['json']['body']
        self.assertEqual(
            [node['content'][0]['text'] for node in body['content']],
            ['Opened a pull request.', 'It adds a retry.'],
        )
        self.assertEqual(
            JiraClient._adf_to_text(body), 'Opened a pull request.\nIt adds a retry.',
        )

    def test_a_rejected_comment_surfaces_jiras_error_text(self) -> None:
        client = _make_client()
        response = _http_error_response(
            400, text='{"errorMessages":["Comment body is not valid!"]}',
        )

        with patch.object(client, '_post', return_value=response):
            with self.assertRaisesRegex(HTTPError, 'Comment body is not valid!'):
                client.add_comment('PROJ-1', 'Ready for review')


class JiraClientAddTagTests(unittest.TestCase):
    def test_sends_add_label_update(self) -> None:
        client = _make_client()
        response = mock_response()

        with patch.object(client, '_put', return_value=response) as mock_put:
            client.add_tag('PROJ-1', 'in-progress')

        mock_put.assert_called_once_with(
            '/rest/api/3/issue/PROJ-1',
            json={'update': {'labels': [{'add': 'in-progress'}]}},
        )
        response.raise_for_status.assert_called_once_with()


class JiraClientRemoveTagTests(unittest.TestCase):
    def test_sends_remove_label_update(self) -> None:
        client = _make_client()
        response = mock_response()

        with patch.object(client, '_put', return_value=response) as mock_put:
            client.remove_tag('PROJ-1', 'in-progress')

        mock_put.assert_called_once_with(
            '/rest/api/3/issue/PROJ-1',
            json={'update': {'labels': [{'remove': 'in-progress'}]}},
        )
        response.raise_for_status.assert_called_once_with()


class JiraClientMoveIssueToStateTests(unittest.TestCase):
    def test_uses_transition_for_status_field(self) -> None:
        client = _make_client()
        transitions_response = mock_response(
            json_data={
                'transitions': [
                    {JiraTransitionFields.ID: '31', JiraTransitionFields.NAME: 'In Review',
                     JiraTransitionFields.TO: {JiraTransitionFields.NAME: 'In Review'}}
                ]
            }
        )
        update_response = mock_response()

        with patch.object(client, '_get', return_value=transitions_response) as mock_get, \
             patch.object(client, '_post', return_value=update_response) as mock_post:
            client.move_issue_to_state('PROJ-1', 'status', 'In Review')

        mock_get.assert_called_once_with('/rest/api/3/issue/PROJ-1/transitions')
        mock_post.assert_called_once_with(
            '/rest/api/3/issue/PROJ-1/transitions',
            json={'transition': {JiraTransitionFields.ID: '31'}},
        )

    def test_uses_transition_for_status_field_case_insensitive(self) -> None:
        client = _make_client()
        transitions_response = mock_response(
            json_data={
                'transitions': [
                    {JiraTransitionFields.ID: '10', JiraTransitionFields.NAME: 'Done',
                     JiraTransitionFields.TO: {JiraTransitionFields.NAME: 'Done'}}
                ]
            }
        )
        update_response = mock_response()

        with patch.object(client, '_get', return_value=transitions_response), \
             patch.object(client, '_post', return_value=update_response):
            client.move_issue_to_state('PROJ-1', 'STATUS', 'Done')

    def test_matches_transition_by_to_name(self) -> None:
        client = _make_client()
        transitions_response = mock_response(
            json_data={
                'transitions': [
                    {JiraTransitionFields.ID: '42', JiraTransitionFields.NAME: 'Transition A',
                     JiraTransitionFields.TO: {JiraTransitionFields.NAME: 'In Review'}}
                ]
            }
        )
        update_response = mock_response()

        with patch.object(client, '_get', return_value=transitions_response), \
             patch.object(client, '_post', return_value=update_response) as mock_post:
            client.move_issue_to_state('PROJ-1', 'status', 'In Review')

        mock_post.assert_called_once_with(
            '/rest/api/3/issue/PROJ-1/transitions',
            json={'transition': {JiraTransitionFields.ID: '42'}},
        )

    def test_raises_when_transition_not_found(self) -> None:
        client = _make_client()
        transitions_response = mock_response(
            json_data={
                'transitions': [
                    {JiraTransitionFields.ID: '31', JiraTransitionFields.NAME: 'Done',
                     JiraTransitionFields.TO: {JiraTransitionFields.NAME: 'Done'}}
                ]
            }
        )

        with patch.object(client, '_get', return_value=transitions_response):
            with self.assertRaisesRegex(ValueError, 'unknown jira transition: In Review'):
                client.move_issue_to_state('PROJ-1', 'status', 'In Review')

    def test_puts_field_update_for_non_status_fields(self) -> None:
        # After the update, move_issue_to_state re-fetches the issue
        # to verify the field actually changed. Both calls must be
        # mocked: the PUT (update) and the GET (verify).
        client = _make_client()
        put_response = mock_response()
        verify_response = mock_response(
            json_data={'fields': {'priority': 'High'}},
        )

        with patch.object(client, '_put', return_value=put_response) as mock_put, \
             patch.object(client, '_get', return_value=verify_response):
            client.move_issue_to_state('PROJ-1', 'priority', 'High')

        mock_put.assert_called_once_with(
            '/rest/api/3/issue/PROJ-1',
            json={'fields': {'priority': 'High'}},
        )

    def test_raises_when_field_update_silently_ignored(self) -> None:
        # The bug this verification catches: Jira accepts the PUT
        # with 200 OK but the field isn't actually updated (read-only
        # field, wrong custom field id, workflow validation). Without
        # the verify, the caller would believe the state changed when it
        # hadn't, leaving the operator's view out of sync with Jira.
        client = _make_client()
        put_response = mock_response()
        verify_response = mock_response(
            json_data={'fields': {'priority': 'Low'}},  # unchanged
        )

        with patch.object(client, '_put', return_value=put_response), \
             patch.object(client, '_get', return_value=verify_response):
            with self.assertRaisesRegex(RuntimeError, 'still'):
                client.move_issue_to_state('PROJ-1', 'priority', 'High')

    def test_handles_empty_transitions_list(self) -> None:
        client = _make_client()
        transitions_response = mock_response(json_data={'transitions': []})

        with patch.object(client, '_get', return_value=transitions_response):
            with self.assertRaisesRegex(ValueError, 'unknown jira transition'):
                client.move_issue_to_state('PROJ-1', 'status', 'In Review')

    def test_verifies_dict_field_value_normalises_to_value_key(self) -> None:
        # When Jira returns the field as a dict (e.g. ``priority``
        # comes back as ``{"value": "High"}`` for some custom field
        # types), the verification path must unwrap it before the
        # equality check — otherwise every non-status update would
        # falsely "fail verification" because ``{"value": ...} != "..."``.
        client = _make_client()
        put_response = mock_response()
        verify_response = mock_response(
            json_data={'fields': {'priority': {'value': 'High'}}},
        )

        with patch.object(client, '_put', return_value=put_response), \
             patch.object(client, '_get', return_value=verify_response):
            # Should NOT raise — the dict-shape field unwraps to "High".
            client.move_issue_to_state('PROJ-1', 'priority', 'High')

    def test_verifies_dict_field_value_normalises_to_name_key(self) -> None:
        # Other Jira custom field types return ``{"name": "..."}``.
        client = _make_client()
        put_response = mock_response()
        verify_response = mock_response(
            json_data={'fields': {'priority': {'name': 'High'}}},
        )

        with patch.object(client, '_put', return_value=put_response), \
             patch.object(client, '_get', return_value=verify_response):
            client.move_issue_to_state('PROJ-1', 'priority', 'High')


class JiraClientBuildRecordTests(unittest.TestCase):
    def test_generates_branch_name_from_id(self) -> None:
        client = _make_client()
        record = client._build_record(
            issue_id='PROJ-42',
            summary='s',
            description='d',
            comment_entries=[],
        )

        self.assertEqual(record.branch_name, 'feature/proj-42')

    def test_uses_explicit_branch_name(self) -> None:
        client = _make_client()
        record = client._build_record(
            issue_id='PROJ-1',
            summary='s',
            description='d',
            comment_entries=[],
            branch_name='custom/branch',
        )

        self.assertEqual(record.branch_name, 'custom/branch')

    def test_sets_all_comments_attribute(self) -> None:
        client = _make_client()
        entries = [{'author': 'bob', 'body': 'nice'}]
        record = client._build_record(
            issue_id='PROJ-1',
            summary='s',
            description='d',
            comment_entries=entries,
        )

        self.assertEqual(getattr(record, ISSUE_ALL_COMMENTS), entries)

    def test_normalizes_none_fields(self) -> None:
        client = _make_client()
        record = client._build_record(
            issue_id='PROJ-1',
            summary=None,
            description=None,
            comment_entries=[],
        )

        self.assertEqual(record.summary, '')
        self.assertEqual(record.description, '')


class JiraClientAdfToTextTests(unittest.TestCase):
    def test_returns_empty_for_none(self) -> None:
        self.assertEqual(JiraClient._adf_to_text(None), '')

    def test_strips_plain_string(self) -> None:
        self.assertEqual(JiraClient._adf_to_text('  hello  '), 'hello')

    def test_joins_list(self) -> None:
        result = JiraClient._adf_to_text(['hello', ' ', 'world'])
        self.assertIn('hello', result)
        self.assertIn('world', result)

    def test_extracts_text_from_paragraph_node(self) -> None:
        node = _adf_paragraph('Hello paragraph')
        result = JiraClient._adf_to_text(node)
        self.assertIn('Hello paragraph', result)

    def test_handles_nested_content(self) -> None:
        node = {
            'type': 'doc',
            'content': [
                {
                    'type': 'paragraph',
                    'content': [
                        {'type': 'text', 'text': 'Line one'},
                        {'type': 'text', 'text': 'Line two'},
                    ],
                }
            ],
        }
        result = JiraClient._adf_to_text(node)
        self.assertIn('Line one', result)
        self.assertIn('Line two', result)

    def test_converts_non_dict_non_list_to_str(self) -> None:
        self.assertEqual(JiraClient._adf_to_text(42), '42')

    def test_uses_newline_separator_for_heading_type(self) -> None:
        node = {
            'type': 'heading',
            'content': [
                {'type': 'text', 'text': 'Title'},
            ],
        }
        result = JiraClient._adf_to_text(node)
        self.assertEqual(result, 'Title')

    def test_handles_dict_node_whose_content_is_not_a_list(self) -> None:
        # Branch 480->485: ``content`` is present but not a list (e.g.
        # malformed payload sends a string). The list-walk is skipped
        # and only the top-level ``text`` field is returned.
        node = {'type': 'paragraph', 'text': 'just text', 'content': 'oops'}
        result = JiraClient._adf_to_text(node)
        self.assertEqual(result, 'just text')

    def test_skips_content_items_that_resolve_to_empty_text(self) -> None:
        # Branch 483->481: an item in ``content`` resolves to empty
        # text (e.g. an empty string node) — loop continues without
        # appending so it doesn't pollute the joined output.
        node = {
            'type': 'paragraph',
            'content': [
                {'type': 'text', 'text': ''},        # empty → skipped
                {'type': 'text', 'text': 'kept'},
            ],
        }
        result = JiraClient._adf_to_text(node)
        self.assertEqual(result, 'kept')

    # ----- code blocks: text and line breaks exactly as written -----

    _CODE = '    indented first line\n\nsecond: {"a": 1}\n  last line\n'

    def test_code_block_keeps_its_text_and_line_breaks_exactly(self) -> None:
        node = {'type': 'codeBlock', 'content': [{'type': 'text', 'text': self._CODE}]}
        self.assertEqual(JiraClient._adf_to_text(node), self._CODE)

    def test_code_block_joins_its_text_nodes_unchanged(self) -> None:
        node = {'type': 'codeBlock', 'content': [
            {'type': 'text', 'text': '  a\n'},
            'not-a-node',
            {'type': 'text'},
            {'type': 'text', 'text': '  b'},
        ]}
        self.assertEqual(JiraClient._adf_to_text(node), '  a\n  b')

    def test_code_block_without_a_content_list_is_empty(self) -> None:
        self.assertEqual(JiraClient._adf_to_text({'type': 'codeBlock'}), '')
        self.assertEqual(
            JiraClient._adf_to_text({'type': 'codeBlock', 'content': 'oops'}), '',
        )

    def test_document_blocks_are_one_per_line(self) -> None:
        node = {'type': 'doc', 'content': [
            {'type': 'paragraph', 'content': [{'type': 'text', 'text': 'First.'}]},
            {'type': 'paragraph', 'content': [{'type': 'text', 'text': 'Second.'}]},
        ]}
        self.assertEqual(JiraClient._adf_to_text(node), 'First.\nSecond.')

    def test_code_block_after_a_paragraph_starts_on_its_own_line(self) -> None:
        node = {'type': 'doc', 'content': [
            {'type': 'paragraph', 'content': [{'type': 'text', 'text': 'Status:'}]},
            {'type': 'codeBlock', 'content': [{'type': 'text', 'text': self._CODE}]},
        ]}
        self.assertEqual(JiraClient._adf_to_text(node), 'Status:\n' + self._CODE)

    def test_code_block_at_the_edges_is_not_trimmed(self) -> None:
        code = {'type': 'codeBlock', 'content': [{'type': 'text', 'text': self._CODE}]}
        self.assertEqual(
            JiraClient._adf_to_text({'type': 'doc', 'content': [code]}), self._CODE,
        )
        self.assertEqual(JiraClient._adf_to_text([code]), self._CODE)


class JiraClientAttachmentTests(unittest.TestCase):
    def test_is_text_attachment_for_text_mime(self) -> None:
        self.assertTrue(
            JiraClient._is_text_attachment({JiraAttachmentFields.MIME_TYPE: 'text/plain'})
        )

    def test_is_text_attachment_for_application_json(self) -> None:
        self.assertTrue(
            JiraClient._is_text_attachment({JiraAttachmentFields.MIME_TYPE: 'application/json'})
        )

    def test_is_not_text_attachment_for_image(self) -> None:
        self.assertFalse(
            JiraClient._is_text_attachment({JiraAttachmentFields.MIME_TYPE: 'image/png'})
        )

    def test_attachment_name_from_filename(self) -> None:
        result = JiraClient._attachment_name({JiraAttachmentFields.FILENAME: 'notes.txt'})
        self.assertEqual(result, 'notes.txt')

    def test_attachment_name_defaults_to_unknown(self) -> None:
        result = JiraClient._attachment_name({})
        self.assertEqual(result, 'unknown')

    def test_screenshot_section_with_size(self) -> None:
        client = _make_client()
        attachments = [
            {
                JiraAttachmentFields.FILENAME: 'shot.png',
                JiraAttachmentFields.MIME_TYPE: 'image/png',
                JiraAttachmentFields.CONTENT: 'https://jira.example/file/shot.png',
                JiraAttachmentFields.SIZE: 9999,
            }
        ]
        lines = client._format_screenshot_attachments(attachments)

        self.assertEqual(len(lines), 1)
        self.assertIn('shot.png', lines[0])
        self.assertIn('9999 bytes', lines[0])

    def test_screenshot_section_without_size(self) -> None:
        client = _make_client()
        attachments = [
            {
                JiraAttachmentFields.FILENAME: 'shot.png',
                JiraAttachmentFields.MIME_TYPE: 'image/png',
                JiraAttachmentFields.CONTENT: 'https://jira.example/file/shot.png',
            }
        ]
        lines = client._format_screenshot_attachments(attachments)

        self.assertIn('image attachment', lines[0])


class ImageAttachmentSourceTests(unittest.TestCase):
    """Where the downloadable screenshots come from.

    Jira's ``content`` URL is absolute but still needs this client's
    credentials, so the agent could never fetch one itself.
    """

    @staticmethod
    def _issue_response(attachments):
        return mock_response(
            json_data={'fields': {JiraIssueFields.ATTACHMENT: attachments}},
        )

    def test_lists_only_image_attachments(self) -> None:
        client = _make_client()
        response = self._issue_response([
            {
                JiraAttachmentFields.FILENAME: 'shot.png',
                JiraAttachmentFields.MIME_TYPE: 'image/png',
                JiraAttachmentFields.CONTENT: 'https://jira.example/file/1',
            },
            {
                JiraAttachmentFields.FILENAME: 'notes.txt',
                JiraAttachmentFields.MIME_TYPE: 'text/plain',
                JiraAttachmentFields.CONTENT: 'https://jira.example/file/2',
            },
        ])

        with patch.object(client, '_get', return_value=response):
            sources = client._image_attachment_sources('PROJ-1')

        self.assertEqual(sources, [
            {'name': 'shot.png', 'url': 'https://jira.example/file/1'},
        ])

    def test_refetches_the_issue_by_id(self) -> None:
        # Re-fetched rather than reused from the queue listing: the download
        # happens when the task is picked up, and the ticket may have gained a
        # screenshot since it was listed.
        client = _make_client()
        with patch.object(
            client, '_get', return_value=self._issue_response([]),
        ) as mock_get:
            client._image_attachment_sources('PROJ-7')

        mock_get.assert_called_once_with(
            '/rest/api/3/issue/PROJ-7',
            params={'fields': JiraIssueFields.ATTACHMENT},
        )

    def test_an_issue_with_no_attachments_is_empty(self) -> None:
        client = _make_client()
        with patch.object(client, '_get', return_value=self._issue_response([])):
            self.assertEqual(client._image_attachment_sources('PROJ-1'), [])

    def test_a_malformed_payload_is_empty_not_a_crash(self) -> None:
        client = _make_client()
        for payload in ({}, {'fields': None}, {'fields': {}}):
            with patch.object(
                client, '_get', return_value=mock_response(json_data=payload),
            ):
                self.assertEqual(client._image_attachment_sources('PROJ-1'), [])

    def test_download_returns_empty_for_blank_url(self) -> None:
        client = _make_client()
        result = client._download_text_attachment(
            '', attachment_name='file.txt', max_chars=1000
        )
        self.assertEqual(result, '')

    def test_download_truncates_at_max_chars(self) -> None:
        client = _make_client()
        long_text = 'x' * 10000
        response = mock_response(text=long_text)

        with patch.object(client.session, 'get', return_value=response):
            result = client._download_text_attachment(
                'https://jira.example/file.txt',
                attachment_name='file.txt',
                max_chars=100,
            )

        self.assertEqual(len(result), 100)

    def test_download_returns_none_on_exception(self) -> None:
        client = _make_client()

        with patch.object(client.session, 'get', side_effect=RuntimeError('boom')):
            result = client._download_text_attachment(
                'https://jira.example/file.txt',
                attachment_name='file.txt',
                max_chars=100,
            )

        self.assertIsNone(result)

    def test_download_falls_back_to_raw_content(self) -> None:
        client = _make_client()
        response = mock_response(text='', content=b'raw bytes')

        with patch.object(client.session, 'get', return_value=response):
            result = client._download_text_attachment(
                'https://jira.example/file.txt',
                attachment_name='file.txt',
                max_chars=1000,
            )

        self.assertEqual(result, 'raw bytes')

    def test_format_text_attachments_shows_failure_when_none_returned(self) -> None:
        client = _make_client()
        attachment = {
            JiraAttachmentFields.FILENAME: 'report.txt',
            JiraAttachmentFields.MIME_TYPE: 'text/plain',
            JiraAttachmentFields.CONTENT: 'https://jira.example/report.txt',
        }

        with patch.object(client.session, 'get', side_effect=RuntimeError('network')):
            lines = client._format_text_attachments([attachment])

        self.assertEqual(len(lines), 1)
        self.assertIn('could not be downloaded', lines[0])

    def test_format_text_attachments_skips_empty_content(self) -> None:
        client = _make_client()
        attachment = {
            JiraAttachmentFields.FILENAME: 'empty.txt',
            JiraAttachmentFields.MIME_TYPE: 'text/plain',
            JiraAttachmentFields.CONTENT: 'https://jira.example/empty.txt',
        }
        response = mock_response(text='', content=b'')

        with patch.object(client.session, 'get', return_value=response):
            lines = client._format_text_attachments([attachment])

        self.assertEqual(lines, [])

    def test_get_attachment_with_retry_uses_session_for_absolute_urls(self) -> None:
        client = _make_client()
        response = mock_response(text='content')

        with patch.object(client.session, 'get', return_value=response) as mock_sess:
            client._get_attachment_with_retry('https://example.com/file.txt')

        mock_sess.assert_called_once()

    def test_get_attachment_with_retry_uses_get_for_relative_paths(self) -> None:
        client = _make_client()
        response = mock_response(text='content')

        with patch.object(client, '_get', return_value=response) as mock_get:
            client._get_attachment_with_retry('/relative/path')

        mock_get.assert_called_once()


class JiraClientStaticHelpersTests(unittest.TestCase):
    def test_build_assigned_tasks_query_formats_jql(self) -> None:
        result = JiraClient._build_assigned_tasks_query('PROJ', 'alice', ['To Do', 'In Progress'])

        self.assertIn('project = "PROJ"', result)
        self.assertIn('assignee = "alice"', result)
        self.assertIn('"To Do", "In Progress"', result)
        self.assertIn('ORDER BY updated DESC', result)

    def test_build_assigned_tasks_query_raises_for_empty_states(self) -> None:
        with self.assertRaisesRegex(ValueError, 'states must not be empty'):
            JiraClient._build_assigned_tasks_query('PROJ', 'alice', [])

    def test_task_tags_extracts_plain_strings(self) -> None:
        result = JiraClient._task_tags(['bug', 'enhancement'])
        self.assertEqual(result, ['bug', 'enhancement'])

    def test_task_tags_extracts_from_dict(self) -> None:
        result = JiraClient._task_tags([{'name': 'backend'}])
        self.assertEqual(result, ['backend'])

    def test_task_tags_returns_empty_for_non_list(self) -> None:
        self.assertEqual(JiraClient._task_tags(None), [])

    def test_task_tags_skips_entries_that_normalize_to_empty(self) -> None:
        # Branch 414->407: a tag value normalizes to '' (e.g. None,
        # whitespace-only string, or dict with no name/label/text) —
        # loop back without appending. Real tags still come through.
        result = JiraClient._task_tags([None, '   ', {'foo': 'bar'}, 'kept'])
        self.assertEqual(result, ['kept'])

    def test_json_items_uses_issues_key(self) -> None:
        response = mock_response(json_data={'issues': [{'key': 'A-1'}]})
        result = JiraClient._json_items(response, items_key='issues')
        self.assertEqual(result, [{'key': 'A-1'}])

    def test_json_items_returns_empty_when_key_missing(self) -> None:
        response = mock_response(json_data={'other': []})
        result = JiraClient._json_items(response, items_key='issues')
        self.assertEqual(result, [])

    def test_json_items_without_items_key_returns_list_payload(self) -> None:
        # Branch 421->425: ``items_key`` is empty (default) — payload
        # (already a list) is returned without the dict-extraction
        # step.
        response = mock_response(json_data=[{'key': 'A-1'}, {'key': 'A-2'}])
        result = JiraClient._json_items(response)
        self.assertEqual(result, [{'key': 'A-1'}, {'key': 'A-2'}])

    def test_safe_dict_returns_dict(self) -> None:
        result = JiraClient._safe_dict({'author': {'name': 'bob'}}, 'author')
        self.assertEqual(result, {'name': 'bob'})

    def test_safe_dict_returns_empty_for_non_dict(self) -> None:
        result = JiraClient._safe_dict({'author': 'bob'}, 'author')
        self.assertEqual(result, {})


class JiraClientIssueCommentParsing(unittest.TestCase):
    def test_extracts_comments_from_fields(self) -> None:
        client = _make_client()
        fields = {
            JiraIssueFields.COMMENT: {'comments': [_comment_payload('Hello')]}
        }
        result = client._issue_comments(fields)
        self.assertEqual(len(result), 1)

    def test_returns_empty_for_missing_comment_key(self) -> None:
        client = _make_client()
        result = client._issue_comments({})
        self.assertEqual(result, [])

    def test_returns_empty_for_non_dict_comment_value(self) -> None:
        client = _make_client()
        result = client._issue_comments({JiraIssueFields.COMMENT: 'bad'})
        self.assertEqual(result, [])

    def test_extracts_attachments_from_fields(self) -> None:
        client = _make_client()
        fields = {
            JiraIssueFields.ATTACHMENT: [{'filename': 'a.txt'}]
        }
        result = client._issue_attachments(fields)
        self.assertEqual(result, [{'filename': 'a.txt'}])

    def test_returns_empty_for_missing_attachment_key(self) -> None:
        client = _make_client()
        result = client._issue_attachments({})
        self.assertEqual(result, [])


class JiraClientFlowTests(unittest.TestCase):
    """A-Z flows: create client, fetch issues, add comment, move state, add/remove tag."""

    def test_full_flow_fetch_issues_with_comments_and_attachments(self) -> None:
        client = JiraClient(
            'https://company.atlassian.net',
            'my-token',
            is_operational_comment=lambda body: body.startswith('[bot]'),
        )

        issues_payload = {
            'issues': [
                {
                    JiraIssueFields.KEY: 'ACME-101',
                    'fields': {
                        JiraIssueFields.SUMMARY: 'Implement caching',
                        JiraIssueFields.DESCRIPTION: _adf_paragraph('Cache the DB calls.'),
                        JiraIssueFields.COMMENT: {
                            'comments': [
                                _comment_payload('[bot] automated check', 'ci-bot'),
                                _comment_payload('LGTM!', 'alice'),
                            ]
                        },
                        JiraIssueFields.ATTACHMENT: [
                            {
                                JiraAttachmentFields.FILENAME: 'spec.txt',
                                JiraAttachmentFields.MIME_TYPE: 'text/plain',
                                JiraAttachmentFields.CONTENT: 'https://jira.example/spec.txt',
                            }
                        ],
                        JiraIssueFields.LABELS: ['performance', 'backend'],
                    },
                }
            ]
        }
        issues_response = mock_response(json_data=issues_payload)
        attachment_response = mock_response(text='Spec content here')

        with patch.object(client, '_get', return_value=issues_response), \
             patch.object(client.session, 'get', return_value=attachment_response):
            records = client.get_assigned_tasks('ACME', 'alice', ['In Progress'])

        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.id, 'ACME-101')
        self.assertEqual(record.summary, 'Implement caching')
        self.assertIn('Cache the DB calls.', record.description)
        self.assertNotIn('[bot] automated check', record.description)
        self.assertIn('alice: LGTM!', record.description)
        self.assertIn(_TEXT_ATTACHMENTS_SECTION_TITLE, record.description)
        self.assertIn('Spec content here', record.description)
        self.assertEqual(record.tags, ['performance', 'backend'])
        self.assertEqual(record.branch_name, 'feature/acme-101')
        all_comments = getattr(record, ISSUE_ALL_COMMENTS)
        self.assertEqual(len(all_comments), 2)

    def test_full_flow_add_comment_move_state_add_remove_tag(self) -> None:
        client = JiraClient('https://company.atlassian.net', 'my-token')

        transitions_response = mock_response(
            json_data={
                'transitions': [
                    {JiraTransitionFields.ID: '21', JiraTransitionFields.NAME: 'Start Progress',
                     JiraTransitionFields.TO: {JiraTransitionFields.NAME: 'In Progress'}}
                ]
            }
        )
        ok_response = mock_response()

        comment_response = mock_response()
        add_tag_response = mock_response()
        remove_tag_response = mock_response()

        with patch.object(client, '_post', side_effect=[comment_response, ok_response]) as mock_post, \
             patch.object(client, '_get', return_value=transitions_response), \
             patch.object(client, '_put', side_effect=[add_tag_response, remove_tag_response]) as mock_put:
            client.add_comment('ACME-1', 'Starting work.')
            client.move_issue_to_state('ACME-1', 'status', 'In Progress')
            client.add_tag('ACME-1', 'in-progress')
            client.remove_tag('ACME-1', 'in-progress')

        self.assertEqual(mock_post.call_count, 2)
        self.assertEqual(mock_put.call_count, 2)
        comment_call = mock_post.call_args_list[0]
        self.assertIn('comment', comment_call.args[0])
        add_tag_call = mock_put.call_args_list[0]
        self.assertIn('add', str(add_tag_call))
        remove_tag_call = mock_put.call_args_list[1]
        self.assertIn('remove', str(remove_tag_call))

    def test_full_flow_validate_then_get_tasks(self) -> None:
        client = JiraClient('https://company.atlassian.net', 'my-token')

        validate_response = mock_response(json_data={'issues': []})
        issues_response = mock_response(
            json_data={
                'issues': [
                    _issue_payload(key='ACME-77', summary='Task A', description=_adf_paragraph('Do it.'))
                ]
            }
        )

        with patch.object(client, '_get', side_effect=[validate_response, issues_response]):
            client.validate_connection('ACME', 'alice', ['To Do'])
            records = client.get_assigned_tasks('ACME', 'alice', ['To Do'])

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].id, 'ACME-77')
        self.assertEqual(records[0].summary, 'Task A')


class JiraClientDefensiveBranchTests(unittest.TestCase):
    """Cover the small ``isinstance``/skip defensive branches."""

    def _client(self):
        return JiraClient('https://jira.example.com', 'jira-token', 'alice@example')

    def test_find_transition_skips_non_dict_entries(self) -> None:
        # Line 140: non-dict transition in the list → skip.
        client = self._client()
        valid_transition = {
            'id': '21',
            'name': 'In Progress',
            'to': {'name': 'In Progress'},
        }
        response = mock_response(json_data={
            'transitions': ['not a dict', valid_transition],
        })
        with patch.object(client, '_get_with_retry', return_value=response):
            t = client._find_transition('ACME-1', 'In Progress')
        self.assertEqual(t['id'], '21')

    def test_find_transition_matches_when_target_is_not_a_dict(self) -> None:
        # Branch 182->184: ``to`` (target) is not a dict — target_name
        # stays '' and the matcher still hits on transition_name.
        client = self._client()
        response = mock_response(json_data={
            'transitions': [
                {'id': '7', 'name': 'In Progress', 'to': 'not-a-dict'},
            ],
        })
        with patch.object(client, '_get_with_retry', return_value=response):
            t = client._find_transition('ACME-2', 'In Progress')
        self.assertEqual(t['id'], '7')

    def test_to_record_coerces_non_dict_fields_to_empty(self) -> None:
        # Line 156: ``fields`` isn't a dict → fall back to {}.
        client = self._client()
        record = client._to_record({
            'key': 'ACME-9', 'fields': 'oops-not-a-dict',
        })
        self.assertEqual(record.id, 'ACME-9')

    def test_normalize_issue_records_skips_when_include_returns_false(self) -> None:
        # Line 349: ``include`` is provided and returns False → skip.
        client = self._client()
        result = client._normalize_issue_records(
            [
                {'key': 'KEEP-1', 'fields': {'summary': 'keep'}},
                {'key': 'DROP-1', 'fields': {'summary': 'drop'}},
            ],
            to_record=lambda item: IssueRecord(
                id=item['key'], summary=item['fields']['summary'], description='',
            ),
            include=lambda item: item['key'].startswith('KEEP'),
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].id, 'KEEP-1')

    def test_comment_lines_skips_non_dict_comment_entries(self) -> None:
        # Line 243: non-dict comment in comments list → skip.
        client = self._client()
        result = client._comment_lines([
            'not a dict',
            {ISSUE_COMMENT_AUTHOR: 'alice', ISSUE_COMMENT_BODY: 'real comment'},
        ])
        self.assertEqual(len(result), 1)
        self.assertIn('real comment', result[0])

    def test_format_screenshot_attachments_skips_non_dict_entries(self) -> None:
        # Line 271: non-dict attachment → skip.
        client = self._client()
        result = client._format_screenshot_attachments([
            'not a dict',
            {'mimeType': 'image/png', 'filename': 'x.png',
             'content': 'http://x', 'size': 100},
        ])
        self.assertEqual(len(result), 1)
        self.assertIn('x.png', result[0])

    def test_normalize_issue_records_skips_non_dict_items(self) -> None:
        # Line 349: non-dict item → skip.
        client = self._client()
        result = client._normalize_issue_records(
            ['junk-not-a-dict', {'key': 'ACME-1', 'fields': {'summary': 's'}}],
            to_record=lambda item: IssueRecord(
                id=item['key'], summary=item['fields']['summary'], description='',
            ),
        )
        self.assertEqual(len(result), 1)

    def test_json_items_returns_empty_when_items_key_set_but_payload_not_dict(self) -> None:
        # Line 379: items_key requested but payload is a list → return [].
        response = mock_response(json_data=['list', 'not', 'dict'])
        result = JiraClient._json_items(response, items_key='issues')
        self.assertEqual(result, [])

    def test_build_comment_entries_skips_non_dict_and_blank_body(self) -> None:
        # Lines 394, 397: non-dict skipped; blank body skipped.
        result = JiraClient._build_comment_entries(
            [
                'not a dict',  # skipped (line 394)
                {'author': 'alice', 'body': ''},  # blank body skipped (line 397)
                {'author': 'bob', 'body': 'real'},
            ],
            extract_body=lambda c: c.get('body', ''),
            extract_author=lambda c: c.get('author', ''),
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][ISSUE_COMMENT_AUTHOR], 'bob')


class JiraMentionFilterWiringTests(unittest.TestCase):
    """Confirms the shared @-mention filter is plumbed into Jira.

    The rule itself is exhaustively tested in
    ``provider_client_base.tests.test_mention_utils`` — these tests
    only verify the wiring exists for Jira so the user-reported bug
    (operator tags an employee, the agent acts on it anyway) is blocked
    on this platform too.
    """

    @staticmethod
    def _adf(text: str) -> dict:
        # Minimal ADF document Jira returns for plain-text comments.
        return {
            'type': 'doc', 'version': 1, 'content': [
                {'type': 'paragraph', 'content': [
                    {'type': 'text', 'text': text},
                ]},
            ],
        }

    def _raw(self, text: str) -> dict:
        return {
            JiraCommentFields.AUTHOR: {JiraCommentFields.DISPLAY_NAME: 'Operator'},
            JiraCommentFields.BODY: self._adf(text),
        }

    @staticmethod
    def _mention_node(account_id: str, text: str) -> dict:
        return {'type': 'mention', 'attrs': {'id': account_id, 'text': text}}

    def _raw_nodes(self, *nodes: dict) -> dict:
        return {
            JiraCommentFields.AUTHOR: {JiraCommentFields.DISPLAY_NAME: 'Operator'},
            JiraCommentFields.BODY: {
                'type': 'doc', 'version': 1, 'content': [
                    {'type': 'paragraph', 'content': list(nodes)},
                ],
            },
        }

    @staticmethod
    def _client(**kwargs) -> JiraClient:
        return JiraClient('https://jira.example', 'jira-token', **kwargs)

    def test_unset_bot_login_attribute_is_empty(self) -> None:
        client = self._client()
        self.assertEqual(client._bot_login, '')

    def test_currentuser_jql_alias_treated_as_unset(self) -> None:
        # ``assignee: currentUser()`` is a JQL function, never a mention.
        client = self._client(bot_login='currentUser()')
        self.assertEqual(client._bot_login, '')

    def test_filter_drops_addressed_to_humans_other_than_bot(self) -> None:
        # Plain @login text (explicit login configured).
        client = self._client(bot_login='botuser')
        entries = client._task_comment_entries([
            self._raw('@alice can you take a look'),       # dropped
            self._raw('this also needs a unit test'),      # kept
            self._raw('@botuser please fix the typo'),    # kept
        ])
        bodies = [e[ISSUE_COMMENT_BODY] for e in entries]
        self.assertIn('this also needs a unit test', bodies)
        self.assertIn('@botuser please fix the typo', bodies)
        self.assertNotIn('@alice can you take a look', bodies)

    def test_mid_sentence_mention_is_dropped(self) -> None:
        client = self._client(bot_login='botuser')
        entries = client._task_comment_entries([
            self._raw('he look yada yda @Alice yes ..'),
        ])
        self.assertEqual(entries, [])

    def test_adf_mention_node_for_human_is_dropped(self) -> None:
        # The real Jira bug: a Cloud mention is an ADF node (not @text),
        # which the plain-text flattening dropped — so a comment tagging a
        # human used to slip through. Resolve the bot's accountId and drop.
        client = self._client()
        with patch.object(
            client, '_fetch_current_user_logins', return_value=('bot-account',),
        ):
            entries = client._task_comment_entries([
                self._raw_nodes(
                    self._mention_node('alice-account', '@Alice'),
                    {'type': 'text', 'text': ' please take a look'},
                ),
            ])
        self.assertEqual(entries, [])

    def test_adf_mention_node_for_bot_is_kept(self) -> None:
        client = self._client()
        with patch.object(
            client, '_fetch_current_user_logins', return_value=('bot-account',),
        ):
            entries = client._task_comment_entries([
                self._raw_nodes(
                    self._mention_node('bot-account', '@Bot User'),
                    {'type': 'text', 'text': ' please fix'},
                ),
            ])
        self.assertEqual(len(entries), 1)

    def test_wiki_markup_mention_for_human_is_dropped(self) -> None:
        # Jira Server stores mentions as wiki markup ``[~username]`` in a
        # plain-string body.
        client = self._client()
        with patch.object(
            client, '_fetch_current_user_logins', return_value=('botuser',),
        ):
            entries = client._task_comment_entries([
                {
                    JiraCommentFields.AUTHOR: {
                        JiraCommentFields.DISPLAY_NAME: 'Operator'},
                    JiraCommentFields.BODY: '[~jsmith] please take this',
                },
            ])
        self.assertEqual(entries, [])

    def test_unset_login_resolved_drops_human_keeps_bot_and_plain(self) -> None:
        client = self._client()
        with patch.object(
            client, '_fetch_current_user_logins', return_value=('botuser',),
        ) as fetch:
            entries = client._task_comment_entries([
                self._raw('@alice please review'),          # dropped
                self._raw('a general note'),                # kept (no mention)
                self._raw('@botuser fix the typo'),        # kept (bot)
            ])
        bodies = [e[ISSUE_COMMENT_BODY] for e in entries]
        self.assertEqual(bodies, ['a general note', '@botuser fix the typo'])
        fetch.assert_called_once()

    def test_filter_noop_when_identity_unresolvable_keeps_all(self) -> None:
        client = self._client()
        with patch.object(
            client, '_fetch_current_user_logins', return_value=(),
        ):
            entries = client._task_comment_entries([self._raw('@alice ping')])
        self.assertEqual(len(entries), 1)

    def test_explicit_login_does_not_resolve(self) -> None:
        client = self._client(bot_login='botuser')
        with patch.object(
            client, '_fetch_current_user_logins',
            side_effect=AssertionError('should not resolve'),
        ):
            entries = client._task_comment_entries([self._raw('@alice ping')])
        self.assertEqual(entries, [])

    # ----- _fetch_current_user_logins -----

    def test_fetch_current_user_logins_all_fields(self) -> None:
        client = self._client()
        with patch.object(
            client, '_get_with_retry',
            return_value=mock_response(json_data={
                'accountId': '557058:ABC', 'name': 'Botuser',
                'displayName': 'Bot User',
            }),
        ) as get:
            self.assertEqual(
                client._fetch_current_user_logins(),
                ('557058:abc', 'botuser', 'bot user'),
            )
        get.assert_called_once_with('/rest/api/3/myself')

    def test_fetch_current_user_logins_subset_of_fields(self) -> None:
        client = self._client()
        with patch.object(
            client, '_get_with_retry',
            return_value=mock_response(json_data={'accountId': '557058:abc'}),
        ):
            self.assertEqual(
                client._fetch_current_user_logins(), ('557058:abc',))

    def test_fetch_current_user_logins_error(self) -> None:
        client = self._client()
        with patch.object(
            client, '_get_with_retry', side_effect=RuntimeError('401'),
        ):
            self.assertEqual(client._fetch_current_user_logins(), ())

    # ----- _adf_mention_ids / _mention_node_ids units -----

    def test_adf_mention_ids_collects_id_and_text(self) -> None:
        ids = JiraClient._adf_mention_ids(
            self._mention_node('acct-1', '@Display Name'))
        self.assertEqual(ids, ['acct-1', 'Display Name'])

    def test_adf_mention_ids_walks_nested_content_and_lists(self) -> None:
        node = {'type': 'doc', 'content': [
            {'type': 'paragraph', 'content': [
                self._mention_node('acct-1', '@A'),
            ]},
            [self._mention_node('acct-2', '@B')],
        ]}
        self.assertEqual(JiraClient._adf_mention_ids(node), ['acct-1', 'A', 'acct-2', 'B'])

    def test_adf_mention_ids_ignores_non_dict_and_bad_attrs(self) -> None:
        self.assertEqual(JiraClient._adf_mention_ids('plain string'), [])
        self.assertEqual(JiraClient._adf_mention_ids(42), [])
        # mention node with no/invalid attrs yields nothing
        self.assertEqual(
            JiraClient._adf_mention_ids({'type': 'mention'}), [])
        self.assertEqual(
            JiraClient._adf_mention_ids(
                {'type': 'mention', 'attrs': 'nope'}), [])
        # mention node with empty id/text yields nothing
        self.assertEqual(
            JiraClient._adf_mention_ids(
                {'type': 'mention', 'attrs': {'id': '', 'text': '@'}}), [])


class JiraClientTagSupportTests(unittest.TestCase):
    def test_it_has_real_tags_so_a_mirror_applies_to_it(self) -> None:
        self.assertTrue(JiraClient.supports_tags)


# ---------------------------------------------------------------------------
# Sub-tasks: create / find / assign / batch status / comments / current user
# ---------------------------------------------------------------------------

_SEARCH = '/rest/api/3/search'
_PARENT = '/rest/api/3/issue/PROJ-1'
_PROJECT = '/rest/api/3/project/PROJ'
_NEW = '/rest/api/3/issue/PROJ-9'
_CODE = '    indented first line\n\n{"done": 3, "left": 1}\n  last line\n'

_COMPANY_MANAGED_TYPES = [
    {'id': '10001', 'name': 'Story', 'subtask': False},
    {'id': '10003', 'name': 'Sub-task', 'subtask': True},
]


def _parent_response(*, subtask: bool = False, project: dict | None = None):
    return mock_response(json_data={'key': 'PROJ-1', 'fields': {
        'project': {'key': 'PROJ', 'id': '10000'} if project is None else project,
        'issuetype': {'id': '10001', 'name': 'Story', 'subtask': subtask},
    }})


def _project_response(issue_types):
    return mock_response(json_data={'key': 'PROJ', 'issueTypes': issue_types})


def _assignee_response(account_id: str = '', name: str = ''):
    assignee = {'accountId': account_id, 'displayName': name} if account_id else None
    return mock_response(json_data={'key': 'PROJ-9', 'fields': {'assignee': assignee}})


def _created_response(key: str = 'PROJ-9'):
    return mock_response(json_data={'id': '10050', 'key': key, 'self': f'https://jira.example/{key}'})


def _search_page(issues: list, *, total: int | None = None, start_at: int = 0):
    payload = {'startAt': start_at, 'maxResults': 100, 'issues': issues}
    payload['total'] = len(issues) if total is None else total
    return mock_response(json_data=payload)


def _search_params(jql: str, fields: str, start_at: int = 0) -> dict:
    return {'jql': jql, 'fields': fields, 'startAt': start_at, 'maxResults': 100}


class JiraClientSubtaskSupportTests(unittest.TestCase):
    def test_it_can_hold_work_as_subtasks(self) -> None:
        self.assertTrue(JiraClient.supports_subtasks)


class JiraClientCreateSubtaskTests(unittest.TestCase):
    _ARGS = ('PROJ-1', 'Write the importer tests', 'Cover the retry.\nref: abc-123')
    _READY = {_PARENT: _parent_response, _PROJECT: lambda: _project_response(_COMPANY_MANAGED_TYPES)}

    def _client(self, routes, *, post=None, put=None) -> JiraClient:
        """A client whose HTTP verbs answer from ``routes`` (path → response)."""
        client = _make_client()
        self.get = patch.object(client, '_get', side_effect=_route_get(routes)).start()
        self.post = patch.object(client, '_post', return_value=post or _created_response()).start()
        self.put = patch.object(client, '_put', return_value=put or mock_response()).start()
        self.addCleanup(patch.stopall)
        return client

    def _ready_routes(self, extra: dict | None = None) -> dict:
        """The parent + project reads every create starts with, plus ``extra``."""
        return {path: build() for path, build in self._READY.items()} | (extra or {})

    def _create(self, routes, **kwargs) -> str:
        return self._client(routes, **kwargs).create_subtask(*self._ARGS)

    def test_creates_an_unassigned_subtask_in_the_parents_project(self) -> None:
        key = self._create(self._ready_routes({_NEW: _assignee_response()}))

        self.assertEqual(key, 'PROJ-9')
        self.assertEqual(self.get.call_args_list, [
            call(_PARENT, params={'fields': 'project,issuetype'}),
            call(_PROJECT),
            call(_NEW, params={'fields': 'assignee'}),
        ])
        self.post.assert_called_once_with('/rest/api/3/issue', json={'fields': {
            'project': {'key': 'PROJ'},
            'parent': {'key': 'PROJ-1'},
            'summary': 'Write the importer tests',
            'description': {'type': 'doc', 'version': 1, 'content': [
                {'type': 'paragraph', 'content': [{'type': 'text', 'text': 'Cover the retry.'}]},
                {'type': 'paragraph', 'content': [{'type': 'text', 'text': 'ref: abc-123'}]},
            ]},
            'issuetype': {'id': '10003'},
            'assignee': None,
        }})
        # Already unassigned: nothing to clear, and no sprint call at all —
        # a Jira sub-task follows its parent's sprint.
        self.put.assert_not_called()

    def test_a_team_managed_subtask_type_is_found_by_its_flag_not_its_name(self) -> None:
        self._create({
            _PARENT: _parent_response(),
            _PROJECT: _project_response([
                {'id': '10020', 'name': 'Task', 'subtask': False},
                'not-a-type',
                {'id': '', 'name': 'Broken', 'subtask': True},
                {'id': '10021', 'name': 'Subtask', 'subtask': True, 'hierarchyLevel': -1},
                {'id': '10022', 'name': 'Sub-task', 'subtask': True},
            ]),
            _NEW: _assignee_response(),
        })

        sent = self.post.call_args.kwargs['json']['fields']
        self.assertEqual(sent['issuetype'], {'id': '10021'})

    def test_a_subtask_cannot_be_split_again(self) -> None:
        client = self._client({_PARENT: _parent_response(subtask=True)})
        with self.assertRaisesRegex(ValueError, 'PROJ-1 is itself a Jira sub-task'):
            client.create_subtask(*self._ARGS)

        self.get.assert_called_once()
        self.post.assert_not_called()

    def test_a_parent_without_a_project_is_an_error(self) -> None:
        client = self._client({_PARENT: _parent_response(project={})})
        with self.assertRaisesRegex(RuntimeError, 'no project for PROJ-1'):
            client.create_subtask(*self._ARGS)

        self.post.assert_not_called()

    def test_a_project_without_a_subtask_type_is_an_error(self) -> None:
        for project in (
            _project_response([{'id': '10001', 'name': 'Story', 'subtask': False}]),
            _project_response('not-a-list'),
            mock_response(json_data=None),
        ):
            with self.subTest(payload=project.json.return_value):
                client = self._client({_PARENT: _parent_response(), _PROJECT: project})
                with self.assertRaisesRegex(RuntimeError, 'PROJ has no sub-task issue type'):
                    client.create_subtask(*self._ARGS)
                self.post.assert_not_called()

    def test_a_create_without_a_key_is_an_error(self) -> None:
        client = self._client(self._ready_routes(), post=mock_response(json_data={}))
        with self.assertRaisesRegex(RuntimeError, 'sub-task of PROJ-1 but returned no key'):
            client.create_subtask(*self._ARGS)

    def test_a_rejected_create_surfaces_jiras_error_text(self) -> None:
        rejected = _http_error_response(
            400, text='{"errors":{"assignee":"Field cannot be set."}}',
        )
        client = self._client(self._ready_routes(), post=rejected)
        with self.assertRaisesRegex(HTTPError, 'Field cannot be set.'):
            client.create_subtask(*self._ARGS)

    def test_a_default_assignee_is_cleared(self) -> None:
        key = self._create(self._ready_routes({
            _NEW: [_assignee_response('acc-lead', 'Project Lead'), _assignee_response()],
        }))

        self.assertEqual(key, 'PROJ-9')
        self.put.assert_called_once_with(
            '/rest/api/3/issue/PROJ-9/assignee', json={'accountId': None},
        )

    def test_still_assigned_after_clearing_raises_naming_the_new_issue(self) -> None:
        client = self._client(self._ready_routes({_NEW: [
            _assignee_response('acc-lead', 'Project Lead'),
            _assignee_response('acc-lead', 'Project Lead'),
        ]}))
        with self.assertRaisesRegex(
            RuntimeError, 'sub-task PROJ-9 but it is still assigned to acc-lead',
        ):
            client.create_subtask(*self._ARGS)

        self.put.assert_called_once()

    def test_a_failed_unassign_raises_naming_the_new_issue(self) -> None:
        refused = _http_error_response(
            400, text='{"errorMessages":["Issues must be assigned."]}',
        )
        client = self._client(
            self._ready_routes({_NEW: _assignee_response('acc-lead', 'Project Lead')}),
            put=refused,
        )
        with self.assertRaisesRegex(
            RuntimeError,
            'sub-task PROJ-9 but could not unassign it from acc-lead: .*Issues must be assigned.',
        ) as raised:
            client.create_subtask(*self._ARGS)

        self.assertIsInstance(raised.exception.__cause__, HTTPError)


class JiraClientFindSubtaskTests(unittest.TestCase):
    _JQL = 'parent = "PROJ-1" ORDER BY created ASC'

    @staticmethod
    def _issue(key: str, description) -> dict:
        return {'key': key, 'fields': {'description': description}}

    def test_returns_the_first_subtask_whose_description_holds_the_marker(self) -> None:
        client = _make_client()
        response = _search_page([
            self._issue('PROJ-7', _adf_paragraph('other work')),
            self._issue('PROJ-8', _adf_paragraph('ref: abc-123')),
            self._issue('PROJ-9', _adf_paragraph('ref: abc-123, a duplicate')),
        ])

        with patch.object(client, '_get', return_value=response) as mock_get:
            key = client.find_subtask('PROJ-1', 'ref: abc-123')

        self.assertEqual(key, 'PROJ-8')
        mock_get.assert_called_once_with(_SEARCH, params=_search_params(self._JQL, 'description'))

    def test_follows_the_pages_until_it_is_found(self) -> None:
        client = _make_client()
        pages = [
            _search_page([
                self._issue('PROJ-7', _adf_paragraph('other work')),
                self._issue('PROJ-8', None),
            ], total=3),
            _search_page([self._issue('PROJ-9', _adf_paragraph('ref: abc-123'))], total=3, start_at=2),
        ]

        with patch.object(client, '_get', side_effect=pages) as mock_get:
            key = client.find_subtask('PROJ-1', 'ref: abc-123')

        self.assertEqual(key, 'PROJ-9')
        self.assertEqual(
            mock_get.call_args_list[1],
            call(_SEARCH, params=_search_params(self._JQL, 'description', start_at=2)),
        )

    def test_no_match_is_empty(self) -> None:
        client = _make_client()
        response = _search_page([
            'not-an-issue',
            self._issue('PROJ-7', 'plain-text description'),
            {'key': 'PROJ-8', 'fields': None},
        ])

        with patch.object(client, '_get', return_value=response):
            self.assertEqual(client.find_subtask('PROJ-1', 'ref: abc-123'), '')

    def test_finds_a_marker_inside_a_code_block(self) -> None:
        client = _make_client()
        description = {'type': 'doc', 'content': [
            {'type': 'codeBlock', 'content': [{'type': 'text', 'text': 'x\nref: abc-123\n'}]},
        ]}

        with patch.object(client, '_get', return_value=_search_page([self._issue('PROJ-9', description)])):
            self.assertEqual(client.find_subtask('PROJ-1', 'ref: abc-123'), 'PROJ-9')

    def test_an_empty_marker_is_refused_without_a_request(self) -> None:
        # An empty marker is in every description: it would pass any sub-task
        # off as the one a retried create is looking for.
        client = _make_client()
        with patch.object(client, '_get') as mock_get:
            for marker in ('', '   '):
                with self.assertRaisesRegex(ValueError, 'marker must not be empty'):
                    client.find_subtask('PROJ-1', marker)
        mock_get.assert_not_called()


class JiraClientAssignIssueTests(unittest.TestCase):
    def test_assigns_by_account_id(self) -> None:
        client = _make_client()
        response = mock_response()

        with patch.object(client, '_put', return_value=response) as mock_put:
            client.assign_issue('PROJ-9', 'acc-1')

        mock_put.assert_called_once_with('/rest/api/3/issue/PROJ-9/assignee', json={'accountId': 'acc-1'})
        response.raise_for_status.assert_called_once_with()

    def test_an_empty_assignee_unassigns(self) -> None:
        client = _make_client()
        for assignee in ('', '  '):
            with patch.object(client, '_put', return_value=mock_response()) as mock_put:
                client.assign_issue('PROJ-9', assignee)
            mock_put.assert_called_once_with('/rest/api/3/issue/PROJ-9/assignee', json={'accountId': None})

    def test_a_refused_assignment_surfaces_jiras_error_text(self) -> None:
        client = _make_client()
        refused = _http_error_response(
            400, text='{"errorMessages":["User cannot be assigned issues."]}',
        )
        with patch.object(client, '_put', return_value=refused):
            with self.assertRaisesRegex(HTTPError, 'User cannot be assigned issues.'):
                client.assign_issue('PROJ-9', 'acc-1')


def _status_issue(
    key: str,
    *,
    status: str = 'In Progress',
    assignee: dict | None = None,
    created: str = '2026-10-17T13:00:00.000+0300',
    parent: str = 'PROJ-1',
    **extra_fields,
) -> dict:
    return {'key': key, 'fields': {
        'status': {'name': status, 'id': '3', 'statusCategory': {'key': 'indeterminate'}},
        'assignee': assignee,
        'created': created,
        'parent': {'key': parent, 'fields': {'summary': 'Parent'}} if parent else None,
        **extra_fields,
    }}


class JiraClientGetIssuesStatusTests(unittest.TestCase):
    _FIELDS = 'status,assignee,created,parent'

    def test_reads_every_issue_in_one_search(self) -> None:
        client = _make_client()
        response = _search_page([
            _status_issue('PROJ-2', assignee={'accountId': 'acc-1', 'displayName': 'Alice'}),
            _status_issue('PROJ-3', status='To Do', parent='', created='2026-10-17T10:00:00.000+0000'),
        ])

        with patch.object(client, '_get', return_value=response) as mock_get:
            statuses = client.get_issues_status(['PROJ-2', 'PROJ-3', 'PROJ-4'], 'status')

        mock_get.assert_called_once_with(_SEARCH, params=_search_params(
            'key in ("PROJ-2", "PROJ-3", "PROJ-4")', self._FIELDS,
        ))
        self.assertEqual(statuses, {
            'PROJ-2': IssueStatus(
                issue_id='PROJ-2', state='In Progress', assignee='acc-1', assignee_name='Alice',
                created_at='2026-10-17T10:00:00+00:00', parent_id='PROJ-1',
            ),
            'PROJ-3': IssueStatus(
                issue_id='PROJ-3', state='To Do', created_at='2026-10-17T10:00:00+00:00',
            ),
            # Not returned by the search: the tracker has no such issue.
            'PROJ-4': IssueStatus(issue_id='PROJ-4', exists=False),
        })

    def test_no_ids_make_no_request(self) -> None:
        client = _make_client()
        with patch.object(client, '_get') as mock_get:
            self.assertEqual(client.get_issues_status([], 'status'), {})
            self.assertEqual(client.get_issues_status(['', '  '], 'status'), {})
        mock_get.assert_not_called()

    def test_any_spelling_of_status_reads_the_status_field(self) -> None:
        client = _make_client()
        for state_field in ('Status', 'STATUS', ' status ', ''):
            with patch.object(client, '_get', return_value=_search_page([_status_issue('PROJ-2')])) as mock_get:
                statuses = client.get_issues_status(['PROJ-2'], state_field)
            with self.subTest(state_field=state_field):
                self.assertEqual(mock_get.call_args.kwargs['params']['fields'], self._FIELDS)
                self.assertEqual(statuses['PROJ-2'].state, 'In Progress')

    def test_a_custom_state_field_reads_its_value(self) -> None:
        client = _make_client()
        response = _search_page([
            _status_issue('PROJ-2', customfield_10010={'value': 'Ready', 'id': '10100'}),
            _status_issue('PROJ-3', customfield_10010='Blocked'),
            _status_issue('PROJ-4'),
        ])

        with patch.object(client, '_get', return_value=response) as mock_get:
            statuses = client.get_issues_status(['PROJ-2', 'PROJ-3', 'PROJ-4'], 'customfield_10010')

        self.assertEqual(
            mock_get.call_args.kwargs['params']['fields'], 'customfield_10010,assignee,created,parent',
        )
        self.assertEqual(
            [statuses[key].state for key in ('PROJ-2', 'PROJ-3', 'PROJ-4')], ['Ready', 'Blocked', ''],
        )

    def test_ids_are_deduplicated_and_matched_case_insensitively(self) -> None:
        client = _make_client()
        response = _search_page([_status_issue('PROJ-2'), _status_issue('PROJ-3'), 'not-an-issue'])

        with patch.object(client, '_get', return_value=response) as mock_get:
            statuses = client.get_issues_status(['proj-2', ' proj-2 ', 'PROJ-3'], 'status')

        self.assertEqual(mock_get.call_args.kwargs['params']['jql'], 'key in ("proj-2", "PROJ-3")')
        self.assertEqual(list(statuses), ['proj-2', 'PROJ-3'])
        self.assertTrue(statuses['proj-2'].exists)
        self.assertEqual(statuses['proj-2'].issue_id, 'proj-2')

    def test_a_moved_issue_reads_as_gone(self) -> None:
        # Jira answers an old key with the issue under its NEW key.
        client = _make_client()
        with patch.object(client, '_get', return_value=_search_page([_status_issue('NEW-5')])):
            statuses = client.get_issues_status(['OLD-5'], 'status')

        self.assertEqual(statuses, {'OLD-5': IssueStatus(issue_id='OLD-5', exists=False)})

    def test_follows_the_search_pages(self) -> None:
        client = _make_client()
        pages = [
            _search_page([_status_issue('PROJ-2')], total=2),
            _search_page([_status_issue('PROJ-3')], total=2, start_at=1),
        ]

        with patch.object(client, '_get', side_effect=pages) as mock_get:
            statuses = client.get_issues_status(['PROJ-2', 'PROJ-3'], 'status')

        self.assertTrue(statuses['PROJ-2'].exists and statuses['PROJ-3'].exists)
        self.assertEqual(mock_get.call_args.kwargs['params']['startAt'], 1)

    def test_a_rejected_batch_falls_back_to_one_read_per_issue(self) -> None:
        # Jira rejects the whole ``key in (...)`` query with a 400 when one
        # key is unknown — one stale id must not hide every good one.
        client = _make_client()
        rejected = _http_error_response(
            400, json_data={'errorMessages': ["An issue with key 'PROJ-4' does not exist for field 'key'."]},
        )
        found = mock_response(json_data=_status_issue('PROJ-2', status='Done'))
        missing = _http_error_response(404)
        routes = {_SEARCH: rejected, '/rest/api/3/issue/PROJ-2': found, '/rest/api/3/issue/PROJ-4': missing}

        with patch.object(client, '_get', side_effect=_route_get(routes)) as mock_get:
            statuses = client.get_issues_status(['PROJ-2', 'PROJ-4'], 'status')

        self.assertEqual(statuses['PROJ-2'].state, 'Done')
        self.assertEqual(statuses['PROJ-4'], IssueStatus(issue_id='PROJ-4', exists=False))
        self.assertEqual(mock_get.call_args_list[1:], [
            call('/rest/api/3/issue/PROJ-2', params={'fields': self._FIELDS}),
            call('/rest/api/3/issue/PROJ-4', params={'fields': self._FIELDS}),
        ])
        missing.raise_for_status.assert_not_called()

    def test_other_search_failures_are_not_swallowed(self) -> None:
        client = _make_client()
        with patch.object(client, '_get', return_value=_http_error_response(401)):
            with self.assertRaises(HTTPError):
                client.get_issues_status(['PROJ-2'], 'status')
        with patch.object(client, '_get', side_effect=RuntimeError('boom')):
            with self.assertRaisesRegex(RuntimeError, 'boom'):
                client.get_issues_status(['PROJ-2'], 'status')

    def test_a_fallback_read_failing_otherwise_raises(self) -> None:
        client = _make_client()
        routes = {_SEARCH: _http_error_response(400), '/rest/api/3/issue/PROJ-2': _http_error_response(403)}

        with patch.object(client, '_get', side_effect=_route_get(routes)):
            with self.assertRaisesRegex(HTTPError, '403'):
                client.get_issues_status(['PROJ-2'], 'status')


def _jira_comment(comment_id: str, created: str, body, *, account_id: str = 'acc-1', name: str = 'Alice') -> dict:
    return {
        'id': comment_id,
        'author': {'accountId': account_id, 'displayName': name, 'active': True},
        'body': _adf_paragraph(body) if isinstance(body, str) else body,
        'created': created,
        'updated': created,
    }


def _comment_page(comments: list, *, total: int | None = None, start_at: int = 0):
    return mock_response(json_data={
        'startAt': start_at,
        'maxResults': 100,
        'total': len(comments) if total is None else total,
        'comments': comments,
    })


class JiraClientListCommentsTests(unittest.TestCase):
    _PATH = '/rest/api/3/issue/PROJ-9/comment'

    def test_reads_every_comment_newest_first(self) -> None:
        client = _make_client()
        # Served oldest first on purpose: the result is newest first anyway.
        response = _comment_page([
            _jira_comment('10001', '2026-10-17T09:00:00.000+0000', 'first'),
            _jira_comment('10002', '2026-10-17T13:30:00.000+0300', 'third', account_id='acc-2', name='Bob'),
            _jira_comment('10003', '2026-10-17T10:00:00.000+0000', 'second'),
        ])

        with patch.object(client, '_get', return_value=response) as mock_get:
            comments = client.list_comments('PROJ-9')

        mock_get.assert_called_once_with(
            self._PATH, params={'orderBy': '-created', 'startAt': 0, 'maxResults': 100},
        )
        self.assertEqual(comments, [
            IssueComment('10002', 'acc-2', 'Bob', 'third', '2026-10-17T10:30:00+00:00'),
            IssueComment('10003', 'acc-1', 'Alice', 'second', '2026-10-17T10:00:00+00:00'),
            IssueComment('10001', 'acc-1', 'Alice', 'first', '2026-10-17T09:00:00+00:00'),
        ])

    def test_follows_the_pages(self) -> None:
        client = _make_client()
        pages = [
            _comment_page([
                _jira_comment('3', '2026-10-17T12:00:00.000+0000', 'c'),
                _jira_comment('2', '2026-10-17T11:00:00.000+0000', 'b'),
            ], total=3),
            _comment_page([_jira_comment('1', '2026-10-17T10:00:00.000+0000', 'a')], total=3, start_at=2),
        ]

        with patch.object(client, '_get', side_effect=pages) as mock_get:
            comments = client.list_comments('PROJ-9')

        self.assertEqual([c.comment_id for c in comments], ['3', '2', '1'])
        self.assertEqual(
            mock_get.call_args_list[1],
            call(self._PATH, params={'orderBy': '-created', 'startAt': 2, 'maxResults': 100}),
        )

    def test_nothing_is_filtered_out(self) -> None:
        # Unlike the comments folded into a task for an agent: a comment
        # tagging another human and an operational one are both listed.
        client = _make_client(bot_login='bot-account', is_operational_comment=lambda body: body.startswith('[bot]'))
        mention = {'type': 'doc', 'content': [{'type': 'paragraph', 'content': [
            {'type': 'mention', 'attrs': {'id': 'acc-alice', 'text': '@Alice'}},
            {'type': 'text', 'text': 'can you take this one?'},
        ]}]}
        response = _comment_page([
            _jira_comment('2', '2026-10-17T11:00:00.000+0000', mention),
            _jira_comment('1', '2026-10-17T10:00:00.000+0000', '[bot] status update'),
        ])

        with patch.object(client, '_get', return_value=response):
            comments = client.list_comments('PROJ-9')

        self.assertEqual([c.body for c in comments], ['can you take this one?', '[bot] status update'])

    def test_a_code_block_reads_back_exactly(self) -> None:
        client = _make_client()
        body = {'type': 'doc', 'version': 1, 'content': [
            {'type': 'paragraph', 'content': [{'type': 'text', 'text': 'Progress:'}]},
            {'type': 'codeBlock', 'content': [{'type': 'text', 'text': _CODE}]},
        ]}

        with patch.object(client, '_get', return_value=_comment_page([_jira_comment('1', '', body)])):
            (comment,) = client.list_comments('PROJ-9')

        self.assertEqual(comment.body, 'Progress:\n' + _CODE)

    def test_malformed_entries_are_skipped_or_read_as_blank(self) -> None:
        client = _make_client()
        response = _comment_page(['not-a-comment', {'id': 10004, 'body': 'plain', 'created': 'yesterday'}])

        with patch.object(client, '_get', return_value=response):
            comments = client.list_comments('PROJ-9')

        self.assertEqual(comments, [IssueComment(comment_id='10004', body='plain')])

    def test_a_failed_read_raises(self) -> None:
        client = _make_client()
        with patch.object(client, '_get', return_value=_http_error_response(404)):
            with self.assertRaises(HTTPError):
                client.list_comments('PROJ-9')


_PREFORMATTED_BODY = {
    'type': 'doc',
    'version': 1,
    'content': [
        {'type': 'paragraph', 'content': [{'type': 'text', 'text': 'Progress:'}]},
        {'type': 'codeBlock', 'content': [{'type': 'text', 'text': _CODE}]},
    ],
}


class JiraClientAddPreformattedCommentTests(unittest.TestCase):
    def test_posts_one_paragraph_then_a_code_block_and_returns_its_id(self) -> None:
        client = _make_client()
        response = mock_response(json_data={'id': '10010', 'body': _PREFORMATTED_BODY})

        with patch.object(client, '_post', return_value=response) as mock_post:
            comment_id = client.add_preformatted_comment('PROJ-9', 'Progress:', _CODE)

        self.assertEqual(comment_id, '10010')
        mock_post.assert_called_once_with(
            '/rest/api/3/issue/PROJ-9/comment', json={'body': _PREFORMATTED_BODY},
        )
        response.raise_for_status.assert_called_once_with()

    def test_an_answer_without_an_id_returns_empty(self) -> None:
        client = _make_client()
        for payload in ({}, None):
            with patch.object(client, '_post', return_value=mock_response(json_data=payload)):
                with self.subTest(payload=payload):
                    self.assertEqual(client.add_preformatted_comment('PROJ-9', 'Progress:', _CODE), '')

    def test_its_lines_read_back_unchanged_from_list_comments(self) -> None:
        client = _make_client()
        with patch.object(client, '_post', return_value=mock_response()) as mock_post:
            client.add_preformatted_comment('PROJ-9', 'Progress:', _CODE)
        posted = mock_post.call_args.kwargs['json']['body']

        stored = _jira_comment('10010', '2026-10-17T10:00:00.000+0000', posted)
        with patch.object(client, '_get', return_value=_comment_page([stored])):
            (comment,) = client.list_comments('PROJ-9')

        intro, preformatted = comment.body.split('\n', 1)
        self.assertEqual(intro, 'Progress:')
        self.assertEqual(preformatted, _CODE)

    def test_a_rejected_comment_surfaces_jiras_error_text(self) -> None:
        client = _make_client()
        rejected = _http_error_response(400, text='{"errorMessages":["INVALID_INPUT"]}')
        with patch.object(client, '_post', return_value=rejected):
            with self.assertRaisesRegex(HTTPError, 'INVALID_INPUT'):
                client.add_preformatted_comment('PROJ-9', 'Progress:', _CODE)


class JiraClientUpdatePreformattedCommentTests(unittest.TestCase):
    def test_rewrites_the_comment_in_place_with_the_same_shape(self) -> None:
        client = _make_client()
        response = mock_response(json_data={'id': '10010'})

        with patch.object(client, '_put', return_value=response) as mock_put, \
             patch.object(client, '_post') as mock_post:
            client.update_preformatted_comment('PROJ-9', '10010', 'Progress:', _CODE)

        mock_put.assert_called_once_with(
            '/rest/api/3/issue/PROJ-9/comment/10010', json={'body': _PREFORMATTED_BODY},
        )
        response.raise_for_status.assert_called_once_with()
        mock_post.assert_not_called()

    def test_a_refused_update_surfaces_jiras_error_text(self) -> None:
        # e.g. the comment is someone else's, or was deleted.
        client = _make_client()
        refused = _http_error_response(
            403, text='{"errorMessages":["You do not have permission to edit this comment."]}',
        )
        with patch.object(client, '_put', return_value=refused):
            with self.assertRaisesRegex(HTTPError, 'do not have permission to edit this comment'):
                client.update_preformatted_comment('PROJ-9', '10010', 'Progress:', _CODE)


class JiraClientCurrentUserTests(unittest.TestCase):
    def test_returns_the_account_id(self) -> None:
        client = _make_client()
        response = mock_response(json_data={'accountId': '557058:abc', 'displayName': 'Bot User'})

        with patch.object(client, '_get', return_value=response) as mock_get:
            self.assertEqual(client.current_user(), '557058:abc')

        mock_get.assert_called_once_with('/rest/api/3/myself')

    def test_a_failed_read_raises_instead_of_guessing(self) -> None:
        client = _make_client()
        with patch.object(client, '_get', return_value=_http_error_response(401)):
            with self.assertRaises(HTTPError):
                client.current_user()
        with patch.object(client, '_get', side_effect=RuntimeError('boom')):
            with self.assertRaisesRegex(RuntimeError, 'boom'):
                client.current_user()

    def test_an_answer_without_an_account_id_raises(self) -> None:
        client = _make_client()
        for payload in ({'displayName': 'Bot User'}, {'accountId': '  '}, None):
            with patch.object(client, '_get', return_value=mock_response(json_data=payload)):
                with self.subTest(payload=payload):
                    with self.assertRaisesRegex(RuntimeError, 'returned no accountId'):
                        client.current_user()


class JiraClientPagingTests(unittest.TestCase):
    """``_next_page``: the next ``startAt`` page, until ``total`` is read."""

    _PARAMS = {'startAt': 0, 'maxResults': 100}

    def test_asks_for_the_next_page_while_items_remain(self) -> None:
        response = mock_response(json_data={'total': 5})
        self.assertEqual(
            JiraClient._next_page(response, '/p', self._PARAMS, [1, 2]),
            ('/p', {'startAt': 2, 'maxResults': 100}),
        )

    def test_stops_at_the_total(self) -> None:
        response = mock_response(json_data={'total': 2})
        self.assertIsNone(JiraClient._next_page(response, '/p', self._PARAMS, [1, 2]))

    def test_stops_on_an_empty_page(self) -> None:
        response = mock_response(json_data={'total': 5})
        self.assertIsNone(JiraClient._next_page(response, '/p', self._PARAMS, []))

    def test_stops_when_jira_does_not_say_how_many(self) -> None:
        for payload in ({}, {'total': '5'}, None):
            with self.subTest(payload=payload):
                response = mock_response(json_data=payload)
                self.assertIsNone(JiraClient._next_page(response, '/p', self._PARAMS, [1]))


class JiraClientIssueReadTests(unittest.TestCase):
    def test_a_non_object_answer_reads_as_no_fields(self) -> None:
        client = _make_client()
        with patch.object(client, '_get', return_value=mock_response(json_data=None)):
            self.assertEqual(client._fetch_issue_field('PROJ-1', 'priority'), '')
