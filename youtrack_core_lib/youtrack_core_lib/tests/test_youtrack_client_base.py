"""Tests for YouTrackClientBase helpers."""
from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from youtrack_core_lib.youtrack_core_lib.client.youtrack_client_base import (
    UNTRUSTED_ISSUE_COMMENTS_SECTION_TITLE,
    UNTRUSTED_SCREENSHOT_ATTACHMENTS_SECTION_TITLE,
    UNTRUSTED_TEXT_ATTACHMENTS_SECTION_TITLE,
    YouTrackClientBase,
)
from youtrack_core_lib.youtrack_core_lib.data.fields import TaskCommentFields
from youtrack_core_lib.youtrack_core_lib.data.task import Task
from youtrack_core_lib.youtrack_core_lib.tests.utils import mock_response

BASE_URL = 'https://youtrack.example'
TOKEN = 'tok'


def _make_base(operational_comment_prefixes=()):
    return YouTrackClientBase(
        BASE_URL,
        TOKEN,
        timeout=5,
        max_retries=1,
        operational_comment_prefixes=operational_comment_prefixes,
    )


class ConstructorTests(unittest.TestCase):
    def test_stores_empty_prefixes_by_default(self):
        client = _make_base()
        self.assertEqual(client._operational_comment_prefixes, ())

    def test_stores_provided_prefixes(self):
        client = _make_base(operational_comment_prefixes=('Prefix A:', 'Prefix B:'))
        self.assertEqual(client._operational_comment_prefixes, ('Prefix A:', 'Prefix B:'))

    def test_none_prefixes_normalised_to_empty_tuple(self):
        client = YouTrackClientBase(BASE_URL, TOKEN, timeout=5, max_retries=1,
                                    operational_comment_prefixes=None)
        self.assertEqual(client._operational_comment_prefixes, ())


class IsOperationalCommentTests(unittest.TestCase):
    def setUp(self):
        self.client = _make_base(operational_comment_prefixes=('Agent:', 'Bot note:'))

    def test_matching_prefix_is_operational(self):
        self.assertTrue(self.client._is_operational_comment('Agent: did something'))

    def test_second_prefix_is_operational(self):
        self.assertTrue(self.client._is_operational_comment('Bot note: something'))

    def test_no_match_is_not_operational(self):
        self.assertFalse(self.client._is_operational_comment('User comment here'))

    def test_empty_string_not_operational(self):
        self.assertFalse(self.client._is_operational_comment(''))

    def test_no_prefixes_configured_never_operational(self):
        client = _make_base()
        self.assertFalse(client._is_operational_comment('Agent: did something'))

    def test_strips_before_checking(self):
        self.assertTrue(self.client._is_operational_comment('  Agent: trimmed'))


class CommentLinesTests(unittest.TestCase):
    def setUp(self):
        self.client = _make_base(operational_comment_prefixes=('Op:',))

    def _entry(self, author, body):
        return {TaskCommentFields.AUTHOR: author, TaskCommentFields.BODY: body}

    def test_formats_comment(self):
        lines = self.client._comment_lines([self._entry('alice', 'hello')])
        self.assertEqual(lines, ['- alice: hello'])

    def test_filters_operational_comment(self):
        lines = self.client._comment_lines([self._entry('bot', 'Op: note')])
        self.assertEqual(lines, [])

    def test_skips_empty_body(self):
        lines = self.client._comment_lines([self._entry('alice', '')])
        self.assertEqual(lines, [])

    def test_skips_non_dict(self):
        lines = self.client._comment_lines(['not a dict'])
        self.assertEqual(lines, [])

    def test_multiple_comments_mixed(self):
        comments = [
            self._entry('alice', 'hello'),
            self._entry('bot', 'Op: filtered'),
            self._entry('bob', 'world'),
        ]
        lines = self.client._comment_lines(comments)
        self.assertEqual(lines, ['- alice: hello', '- bob: world'])

    def test_unknown_author_fallback(self):
        lines = self.client._comment_lines([{TaskCommentFields.BODY: 'msg'}])
        self.assertEqual(lines, ['- unknown: msg'])


class BuildCommentEntriesTests(unittest.TestCase):
    def _entry(self, text, login='user'):
        return {'text': text, 'author': {'name': login}}

    def test_builds_entry(self):
        entries = YouTrackClientBase._build_comment_entries(
            [self._entry('hello', 'alice')],
            extract_body=lambda c: c['text'],
            extract_author=lambda c: c['author']['name'],
        )
        self.assertEqual(entries, [{'author': 'alice', 'body': 'hello'}])

    def test_skips_empty_body(self):
        entries = YouTrackClientBase._build_comment_entries(
            [self._entry('', 'alice')],
            extract_body=lambda c: c['text'],
            extract_author=lambda c: c['author']['name'],
        )
        self.assertEqual(entries, [])

    def test_skips_non_dict(self):
        entries = YouTrackClientBase._build_comment_entries(
            ['not a dict'],
            extract_body=lambda c: c['text'],
            extract_author=lambda c: c['author']['name'],
        )
        self.assertEqual(entries, [])

    def test_skip_callback_respected(self):
        comments = [self._entry('keep'), self._entry('skip')]
        entries = YouTrackClientBase._build_comment_entries(
            comments,
            extract_body=lambda c: c['text'],
            extract_author=lambda c: 'u',
            skip=lambda c: c['text'] == 'skip',
        )
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['body'], 'keep')

    def test_none_author_replaced_with_unknown(self):
        entries = YouTrackClientBase._build_comment_entries(
            [{'text': 'msg'}],
            extract_body=lambda c: c['text'],
            extract_author=lambda c: None,
        )
        self.assertEqual(entries[0]['author'], 'unknown')


class TaskCommentEntryTests(unittest.TestCase):
    def test_valid_entry(self):
        entry = YouTrackClientBase._task_comment_entry('alice', 'hello')
        self.assertEqual(entry, {'author': 'alice', 'body': 'hello'})

    def test_empty_body_returns_none(self):
        self.assertIsNone(YouTrackClientBase._task_comment_entry('alice', ''))

    def test_none_body_returns_none(self):
        self.assertIsNone(YouTrackClientBase._task_comment_entry('alice', None))

    def test_none_author_becomes_unknown(self):
        entry = YouTrackClientBase._task_comment_entry(None, 'msg')
        self.assertEqual(entry['author'], 'unknown')

    def test_strips_author_and_body(self):
        entry = YouTrackClientBase._task_comment_entry('  alice  ', '  msg  ')
        self.assertEqual(entry, {'author': 'alice', 'body': 'msg'})


class BuildTaskTests(unittest.TestCase):
    def setUp(self):
        self.client = _make_base()

    def test_builds_task_fields(self):
        task = self.client._build_task(
            issue_id='X-1',
            summary='Do thing',
            description='Details',
            comment_entries=[],
        )
        self.assertEqual(task.id, 'X-1')
        self.assertEqual(task.summary, 'Do thing')
        self.assertEqual(task.description, 'Details')

    def test_default_branch_name_derived_from_id(self):
        task = self.client._build_task(
            issue_id='PROJ-42',
            summary='s',
            description='d',
            comment_entries=[],
        )
        self.assertEqual(task.branch_name, 'feature/proj-42')

    def test_explicit_branch_name_used(self):
        task = self.client._build_task(
            issue_id='PROJ-42',
            summary='s',
            description='d',
            comment_entries=[],
            branch_name='custom/branch',
        )
        self.assertEqual(task.branch_name, 'custom/branch')

    def test_tags_set(self):
        task = self.client._build_task(
            issue_id='X-1', summary='s', description='d',
            comment_entries=[], tags=['bug', 'urgent'],
        )
        self.assertEqual(task.tags, ['bug', 'urgent'])

    def test_comments_set_on_task(self):
        entries = [{'author': 'alice', 'body': 'note'}]
        task = self.client._build_task(
            issue_id='X-1', summary='s', description='d',
            comment_entries=entries,
        )
        self.assertEqual(getattr(task, TaskCommentFields.ALL_COMMENTS), entries)


class BuildTaskDescriptionTests(unittest.TestCase):
    def setUp(self):
        self.client = _make_base()

    def test_description_only(self):
        result = self.client._build_task_description_with_attachment_sections(
            'Main description', [], text_attachment_lines=[], screenshot_lines=[],
        )
        self.assertEqual(result, 'Main description')

    def test_no_description_fallback(self):
        result = self.client._build_task_description_with_attachment_sections(
            None, [], text_attachment_lines=[], screenshot_lines=[],
        )
        self.assertIn('No description provided', result)

    def test_includes_comment_section(self):
        comments = [{'author': 'alice', 'body': 'note'}]
        result = self.client._build_task_description_with_attachment_sections(
            'Desc', comments, text_attachment_lines=[], screenshot_lines=[],
        )
        self.assertIn(UNTRUSTED_ISSUE_COMMENTS_SECTION_TITLE, result)
        self.assertIn('alice', result)

    def test_includes_text_attachment_section(self):
        result = self.client._build_task_description_with_attachment_sections(
            'Desc', [], text_attachment_lines=['Attachment file.txt:\ncontent'], screenshot_lines=[],
        )
        self.assertIn(UNTRUSTED_TEXT_ATTACHMENTS_SECTION_TITLE, result)
        self.assertIn('file.txt', result)

    def test_includes_screenshot_section(self):
        result = self.client._build_task_description_with_attachment_sections(
            'Desc', [], text_attachment_lines=[], screenshot_lines=['- img.png'],
        )
        self.assertIn(UNTRUSTED_SCREENSHOT_ATTACHMENTS_SECTION_TITLE, result)

    def test_operational_comments_excluded_from_description(self):
        client = _make_base(operational_comment_prefixes=('Op:',))
        comments = [
            {'author': 'alice', 'body': 'real note'},
            {'author': 'bot', 'body': 'Op: filtered'},
        ]
        result = client._build_task_description_with_attachment_sections(
            'Desc', comments, text_attachment_lines=[], screenshot_lines=[],
        )
        self.assertIn('real note', result)
        self.assertNotIn('Op: filtered', result)

    def test_sections_joined_with_double_newline(self):
        comments = [{'author': 'alice', 'body': 'note'}]
        result = self.client._build_task_description_with_attachment_sections(
            'Desc', comments, text_attachment_lines=[], screenshot_lines=[],
        )
        self.assertIn('\n\n', result)


class JsonItemsTests(unittest.TestCase):
    def test_list_response(self):
        resp = mock_response(json_data=[{'a': 1}, {'b': 2}])
        items = YouTrackClientBase._json_items(resp)
        self.assertEqual(items, [{'a': 1}, {'b': 2}])

    def test_empty_list(self):
        resp = mock_response(json_data=[])
        self.assertEqual(YouTrackClientBase._json_items(resp), [])

    def test_dict_response_no_key(self):
        resp = mock_response(json_data={'k': []})
        self.assertEqual(YouTrackClientBase._json_items(resp), [])

    def test_items_key_extracts_list(self):
        resp = mock_response(json_data={'items': [{'x': 1}]})
        items = YouTrackClientBase._json_items(resp, items_key='items')
        self.assertEqual(items, [{'x': 1}])

    def test_items_key_missing_returns_empty(self):
        resp = mock_response(json_data={'other': []})
        items = YouTrackClientBase._json_items(resp, items_key='items')
        self.assertEqual(items, [])

    def test_non_list_non_dict_returns_empty(self):
        resp = mock_response(json_data='bad')
        self.assertEqual(YouTrackClientBase._json_items(resp), [])

    def test_none_response_returns_empty(self):
        resp = mock_response(json_data=None)
        self.assertEqual(YouTrackClientBase._json_items(resp), [])


class IsTextAttachmentMimeTypeTests(unittest.TestCase):
    def test_text_plain(self):
        self.assertTrue(YouTrackClientBase._is_text_attachment_mime_type('text/plain'))

    def test_text_html(self):
        self.assertTrue(YouTrackClientBase._is_text_attachment_mime_type('text/html'))

    def test_application_json(self):
        self.assertTrue(YouTrackClientBase._is_text_attachment_mime_type('application/json'))

    def test_application_xml(self):
        self.assertTrue(YouTrackClientBase._is_text_attachment_mime_type('application/xml'))

    def test_application_yaml(self):
        self.assertTrue(YouTrackClientBase._is_text_attachment_mime_type('application/yaml'))

    def test_image_not_text(self):
        self.assertFalse(YouTrackClientBase._is_text_attachment_mime_type('image/png'))

    def test_empty_not_text(self):
        self.assertFalse(YouTrackClientBase._is_text_attachment_mime_type(''))

    def test_none_not_text(self):
        self.assertFalse(YouTrackClientBase._is_text_attachment_mime_type(None))


class AttachmentDownloadFailureTextTests(unittest.TestCase):
    def test_contains_name(self):
        text = YouTrackClientBase._attachment_download_failure_text('report.txt')
        self.assertIn('report.txt', text)


class NormalizeIssueTasksTests(unittest.TestCase):
    def setUp(self):
        self.client = _make_base()

    def test_empty_items(self):
        self.assertEqual(self.client._normalize_issue_tasks([], to_task=lambda x: x), [])

    def test_non_dict_items_skipped(self):
        tasks = self.client._normalize_issue_tasks(
            ['not a dict', 42],
            to_task=lambda x: Task(id=x['id']),
        )
        self.assertEqual(tasks, [])

    def test_include_filter_applied(self):
        items = [{'id': 'keep'}, {'id': 'drop'}]
        tasks = self.client._normalize_issue_tasks(
            items,
            to_task=lambda x: Task(id=x['id']),
            include=lambda x: x['id'] == 'keep',
        )
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].id, 'keep')

    def test_exception_in_to_task_skips_item(self):
        def bad_task(item):
            raise ValueError('bad')

        tasks = self.client._normalize_issue_tasks([{'id': 'X'}], to_task=bad_task)
        self.assertEqual(tasks, [])


class SetTaskCommentsTests(unittest.TestCase):
    def test_sets_all_comments_attribute(self):
        client = _make_base()
        task = Task(id='T-1')
        entries = [{'author': 'a', 'body': 'b'}]
        client._set_task_comments(task, entries)
        self.assertEqual(getattr(task, TaskCommentFields.ALL_COMMENTS), entries)


class AbstractInterfaceTests(unittest.TestCase):
    def setUp(self):
        self.client = _make_base()

    def test_validate_connection_raises(self):
        with self.assertRaises(NotImplementedError):
            self.client.validate_connection('P', 'me', ['Open'])

    def test_get_assigned_tasks_raises(self):
        with self.assertRaises(NotImplementedError):
            self.client.get_assigned_tasks('P', 'me', ['Open'])

    def test_add_comment_raises(self):
        with self.assertRaises(NotImplementedError):
            self.client.add_comment('P-1', 'msg')

    def test_add_tag_raises(self):
        with self.assertRaises(NotImplementedError):
            self.client.add_tag('P-1', 'tag')

    def test_remove_tag_raises(self):
        with self.assertRaises(NotImplementedError):
            self.client.remove_tag('P-1', 'tag')

    def test_move_issue_to_state_raises(self):
        with self.assertRaises(NotImplementedError):
            self.client.move_issue_to_state('P-1', 'State', 'Done')


class SectionTitleConstantsTests(unittest.TestCase):
    def test_issue_comments_title_not_empty(self):
        self.assertTrue(UNTRUSTED_ISSUE_COMMENTS_SECTION_TITLE)

    def test_text_attachments_title_not_empty(self):
        self.assertTrue(UNTRUSTED_TEXT_ATTACHMENTS_SECTION_TITLE)

    def test_screenshot_attachments_title_not_empty(self):
        self.assertTrue(UNTRUSTED_SCREENSHOT_ATTACHMENTS_SECTION_TITLE)

    def test_titles_are_distinct(self):
        titles = {
            UNTRUSTED_ISSUE_COMMENTS_SECTION_TITLE,
            UNTRUSTED_TEXT_ATTACHMENTS_SECTION_TITLE,
            UNTRUSTED_SCREENSHOT_ATTACHMENTS_SECTION_TITLE,
        }
        self.assertEqual(len(titles), 3)


if __name__ == '__main__':
    unittest.main()
