"""Tests for youtrack_core_lib.data.task and data.fields."""
from __future__ import annotations

import unittest

from youtrack_core_lib.youtrack_core_lib.data.fields import (
    TaskCommentFields,
    YouTrackAttachmentFields,
    YouTrackCommentFields,
    YouTrackCustomFieldFields,
    YouTrackTagFields,
)
from youtrack_core_lib.youtrack_core_lib.data.task import Task


class TaskConstructionTests(unittest.TestCase):
    def test_defaults(self):
        t = Task()
        self.assertEqual(t.id, '')
        self.assertEqual(t.summary, '')
        self.assertEqual(t.description, '')
        self.assertEqual(t.branch_name, '')
        self.assertEqual(t.tags, [])

    def test_positional_args(self):
        t = Task('ID-1', 'Summary', 'Desc', 'branch', ['tag1'])
        self.assertEqual(t.id, 'ID-1')
        self.assertEqual(t.summary, 'Summary')
        self.assertEqual(t.description, 'Desc')
        self.assertEqual(t.branch_name, 'branch')
        self.assertEqual(t.tags, ['tag1'])

    def test_none_tags_becomes_empty_list(self):
        t = Task(tags=None)
        self.assertEqual(t.tags, [])

    def test_tags_list_copied(self):
        src = ['a', 'b']
        t = Task(tags=src)
        src.append('c')
        self.assertEqual(t.tags, ['a', 'b'])

    def test_fields_settable(self):
        t = Task()
        t.id = 'X-1'
        t.summary = 's'
        t.description = 'd'
        t.branch_name = 'br'
        t.tags = ['t']
        self.assertEqual(t.id, 'X-1')
        self.assertEqual(t.summary, 's')
        self.assertEqual(t.description, 'd')
        self.assertEqual(t.branch_name, 'br')
        self.assertEqual(t.tags, ['t'])


class TaskEqualityTests(unittest.TestCase):
    def _task(self, **kwargs):
        defaults = dict(id='A-1', summary='s', description='d', branch_name='b', tags=[])
        defaults.update(kwargs)
        return Task(**defaults)

    def test_equal_tasks(self):
        self.assertEqual(self._task(), self._task())

    def test_different_id(self):
        self.assertNotEqual(self._task(id='A-1'), self._task(id='A-2'))

    def test_different_summary(self):
        self.assertNotEqual(self._task(summary='a'), self._task(summary='b'))

    def test_different_description(self):
        self.assertNotEqual(self._task(description='a'), self._task(description='b'))

    def test_different_branch_name(self):
        self.assertNotEqual(self._task(branch_name='a'), self._task(branch_name='b'))

    def test_different_tags(self):
        self.assertNotEqual(self._task(tags=['a']), self._task(tags=['b']))

    def test_not_equal_to_non_task(self):
        self.assertNotEqual(self._task(), 'not a task')

    def test_not_equal_to_none(self):
        self.assertNotEqual(self._task(), None)


class TaskReprTests(unittest.TestCase):
    def test_repr_contains_id(self):
        t = Task(id='PROJ-1')
        self.assertIn('PROJ-1', repr(t))

    def test_repr_contains_class_name(self):
        self.assertIn('Task(', repr(Task()))

    def test_repr_contains_all_fields(self):
        t = Task(id='A', summary='B', description='C', branch_name='D', tags=['E'])
        r = repr(t)
        self.assertIn('id=', r)
        self.assertIn('summary=', r)
        self.assertIn('description=', r)
        self.assertIn('branch_name=', r)
        self.assertIn('tags=', r)


class TaskDescriptorAccessTests(unittest.TestCase):
    def test_descriptor_accessed_from_class_returns_descriptor(self):
        from youtrack_core_lib.youtrack_core_lib.data.task import _RecordField
        self.assertIsInstance(Task.id, _RecordField)

    def test_descriptor_key_attribute(self):
        from youtrack_core_lib.youtrack_core_lib.data.task import _RecordField
        self.assertIsInstance(Task.summary, _RecordField)
        self.assertEqual(Task.summary.key, 'summary')

    def test_dynamic_attribute_setattr(self):
        t = Task()
        setattr(t, 'all_comments', [{'author': 'a', 'body': 'b'}])
        self.assertEqual(t.all_comments, [{'author': 'a', 'body': 'b'}])


class TaskCommentFieldsTests(unittest.TestCase):
    def test_author(self):
        self.assertEqual(TaskCommentFields.AUTHOR, 'author')

    def test_body(self):
        self.assertEqual(TaskCommentFields.BODY, 'body')

    def test_all_comments(self):
        self.assertEqual(TaskCommentFields.ALL_COMMENTS, 'all_comments')


class YouTrackAttachmentFieldsTests(unittest.TestCase):
    def test_id(self):
        self.assertEqual(YouTrackAttachmentFields.ID, 'id')

    def test_name(self):
        self.assertEqual(YouTrackAttachmentFields.NAME, 'name')

    def test_mime_type(self):
        self.assertEqual(YouTrackAttachmentFields.MIME_TYPE, 'mimeType')

    def test_charset(self):
        self.assertEqual(YouTrackAttachmentFields.CHARSET, 'charset')

    def test_metadata(self):
        self.assertEqual(YouTrackAttachmentFields.METADATA, 'metaData')

    def test_url(self):
        self.assertEqual(YouTrackAttachmentFields.URL, 'url')


class YouTrackCommentFieldsTests(unittest.TestCase):
    def test_id(self):
        self.assertEqual(YouTrackCommentFields.ID, 'id')

    def test_text(self):
        self.assertEqual(YouTrackCommentFields.TEXT, 'text')

    def test_author(self):
        self.assertEqual(YouTrackCommentFields.AUTHOR, 'author')

    def test_login(self):
        self.assertEqual(YouTrackCommentFields.LOGIN, 'login')

    def test_name(self):
        self.assertEqual(YouTrackCommentFields.NAME, 'name')


class YouTrackCustomFieldFieldsTests(unittest.TestCase):
    def test_id(self):
        self.assertEqual(YouTrackCustomFieldFields.ID, 'id')

    def test_name(self):
        self.assertEqual(YouTrackCustomFieldFields.NAME, 'name')

    def test_type(self):
        self.assertEqual(YouTrackCustomFieldFields.TYPE, '$type')


class YouTrackTagFieldsTests(unittest.TestCase):
    def test_name(self):
        self.assertEqual(YouTrackTagFields.NAME, 'name')


if __name__ == '__main__':
    unittest.main()
