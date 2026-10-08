from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from local_task_core_lib.local_task_core_lib.data.local_task import (
    LocalComment,
    LocalTask,
    LocalTaskState,
)
from local_task_core_lib.local_task_core_lib.store import (
    DEFAULT_ID_PREFIX,
    LocalTaskNotFoundError,
    LocalTaskStore,
    LocalTaskStoreError,
)


class _Clock(object):
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        self.now += 1.0
        return self.now


class _StoreTest(unittest.TestCase):

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / 'nested' / 'tasks.json'
        self.clock = _Clock()
        self.store = LocalTaskStore(self.path, clock=self.clock)


class CreateTests(_StoreTest):

    def test_ids_count_up_with_the_prefix(self) -> None:
        first = self.store.create('Add retry to the export job', 'It times out.')
        second = self.store.create('Second')
        self.assertEqual((first.id, second.id), ('LOCAL-1', 'LOCAL-2'))
        self.assertEqual(first.description, 'It times out.')
        self.assertEqual(first.state, LocalTaskState.OPEN)
        self.assertEqual((first.created_at, first.updated_at), (101.0, 101.0))
        self.assertTrue(self.path.is_file())

    def test_summary_is_required_and_text_is_trimmed(self) -> None:
        with self.assertRaisesRegex(ValueError, 'summary'):
            self.store.create('   ')
        task = self.store.create('  Title  ', '  body  ')
        self.assertEqual((task.summary, task.description), ('Title', 'body'))

    def test_tags_are_deduplicated_ignoring_case_and_blanks(self) -> None:
        task = self.store.create('T', tags=['repo:a', 'REPO:A', '', '  ', 'repo:b'])
        self.assertEqual(task.tags, ['repo:a', 'repo:b'])

    def test_a_starting_state_can_be_given(self) -> None:
        task = self.store.create('T', state=LocalTaskState.IN_PROGRESS)
        self.assertEqual(self.store.get(task.id).state, LocalTaskState.IN_PROGRESS)

    def test_a_deleted_id_is_never_handed_out_again(self) -> None:
        self.store.create('one')
        two = self.store.create('two')
        self.assertTrue(self.store.delete(two.id))
        self.assertEqual(self.store.create('three').id, 'LOCAL-3')

    def test_the_counter_never_goes_below_an_existing_id(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({
            'next_id': 2, 'tasks': [{'id': 'LOCAL-7', 'summary': 'hand-edited'}],
        }), encoding='utf-8')
        self.assertEqual(self.store.create('next').id, 'LOCAL-8')

    def test_a_custom_prefix(self) -> None:
        store = LocalTaskStore(self.path, id_prefix=' plan ')
        self.assertEqual(store.id_prefix, 'PLAN')
        self.assertEqual(store.create('x').id, 'PLAN-1')

    def test_a_prefix_must_be_letters_and_digits(self) -> None:
        for bad in ('', 'L-1', '1ABC', 'a b'):
            with self.assertRaises(ValueError):
                LocalTaskStore(self.path, id_prefix=bad)
        self.assertEqual(DEFAULT_ID_PREFIX, 'LOCAL')


class ReadTests(_StoreTest):

    def test_a_missing_file_is_an_empty_store(self) -> None:
        self.assertEqual(self.store.list(), [])
        self.assertIsNone(self.store.get('LOCAL-1'))
        self.assertEqual(self.store.path, self.path)

    def test_get_ignores_case_and_whitespace(self) -> None:
        task = self.store.create('T')
        self.assertEqual(self.store.get(' local-1 ').id, task.id)
        self.assertIsNone(self.store.get(None))

    def test_list_keeps_creation_order_and_filters_by_state(self) -> None:
        a = self.store.create('a')
        b = self.store.create('b', state=LocalTaskState.REVIEW)
        c = self.store.create('c')
        self.assertEqual([t.id for t in self.store.list()], [a.id, b.id, c.id])
        open_ids = [t.id for t in self.store.list([LocalTaskState.OPEN])]
        self.assertEqual(open_ids, [a.id, c.id])
        self.assertEqual(self.store.list([]), [])

    def test_owns_names_only_ids_of_this_store(self) -> None:
        self.assertTrue(self.store.owns('LOCAL-12'))
        self.assertTrue(self.store.owns(' local-3 '))
        for other in ('UNA-12', 'LOCAL-', 'LOCAL-1a', 'XLOCAL-1', '', None):
            self.assertFalse(self.store.owns(other), other)

    def test_another_instance_sees_every_write_at_once(self) -> None:
        reader = LocalTaskStore(self.path)
        task = self.store.create('T')
        self.store.set_state(task.id, LocalTaskState.REVIEW)
        self.assertEqual(reader.get(task.id).state, LocalTaskState.REVIEW)


class UnreadableFileTests(_StoreTest):

    def _corrupt(self, text: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(text, encoding='utf-8')

    def test_garbage_raises_and_is_never_overwritten(self) -> None:
        for text in ('not json', '[]', '{"tasks": 3}', '{"no_tasks": []}', '{"tasks": [{}]}'):
            self._corrupt(text)
            with self.assertRaises(LocalTaskStoreError):
                self.store.list()
            with self.assertRaises(LocalTaskStoreError):
                self.store.create('would wipe the file')
            self.assertEqual(self.path.read_text(encoding='utf-8'), text)

    def test_a_read_error_is_reported(self) -> None:
        with patch.object(Path, 'read_text', side_effect=PermissionError('denied')):
            with self.assertRaisesRegex(LocalTaskStoreError, 'denied'):
                self.store.list()


class ChangeTests(_StoreTest):

    def setUp(self) -> None:
        super().setUp()
        self.task = self.store.create('T', tags=['keep'])

    def test_state_changes_and_stamps_the_update(self) -> None:
        moved = self.store.set_state('local-1', LocalTaskState.DONE)
        self.assertEqual(moved.state, LocalTaskState.DONE)
        self.assertGreater(moved.updated_at, self.task.updated_at)
        self.assertEqual(self.store.get(self.task.id).state, LocalTaskState.DONE)

    def test_comments_append_in_order(self) -> None:
        self.store.add_comment(self.task.id, ' first ', author='agent', author_id='bot')
        task = self.store.add_comment(self.task.id, 'second')
        self.assertEqual([c.body for c in task.comments], ['first', 'second'])
        self.assertEqual((task.comments[0].author, task.comments[0].author_id), ('agent', 'bot'))
        with self.assertRaisesRegex(ValueError, 'body'):
            self.store.add_comment(self.task.id, '  ')

    def test_tags_add_once_and_remove_ignoring_case(self) -> None:
        self.store.add_tag(self.task.id, 'new')
        task = self.store.add_tag(self.task.id, 'NEW')
        self.assertEqual(task.tags, ['keep', 'new'])
        task = self.store.remove_tag(self.task.id, 'Keep')
        self.assertEqual(task.tags, ['new'])
        self.assertEqual(self.store.remove_tag(self.task.id, 'absent').tags, ['new'])

    def test_an_unknown_task_raises_and_changes_nothing(self) -> None:
        before = self.path.read_text(encoding='utf-8')
        for change in (
            lambda: self.store.set_state('LOCAL-9', LocalTaskState.DONE),
            lambda: self.store.add_comment('LOCAL-9', 'x'),
            lambda: self.store.add_tag('LOCAL-9', 'x'),
            lambda: self.store.remove_tag('LOCAL-9', 'x'),
        ):
            with self.assertRaises(LocalTaskNotFoundError):
                change()
        self.assertFalse(self.store.delete('LOCAL-9'))
        self.assertEqual(self.path.read_text(encoding='utf-8'), before)


class DataTests(unittest.TestCase):

    def test_a_task_round_trips(self) -> None:
        task = LocalTask(
            id='LOCAL-1', summary='s', description='d', tags=['a'],
            state=LocalTaskState.REVIEW,
            comments=[LocalComment('hi', 'agent', 'bot', 3.0)],
            created_at=1.0, updated_at=2.0,
        )
        self.assertEqual(LocalTask.from_dict(json.loads(json.dumps(task.to_dict()))), task)

    def test_missing_and_unknown_fields_take_defaults(self) -> None:
        task = LocalTask.from_dict({
            'id': 'LOCAL-1', 'state': 'archived', 'tags': ['a', ' '],
            'comments': [{'body': 'x'}, 'junk'], 'future_field': 1,
        })
        self.assertEqual(task.state, LocalTaskState.OPEN)
        self.assertEqual(task.tags, ['a'])
        self.assertEqual(task.comments, [LocalComment(body='x')])
        self.assertEqual((task.summary, task.description), ('', ''))
        self.assertEqual(LocalTask.from_dict({'id': 'LOCAL-2'}).state, LocalTaskState.OPEN)

    def test_has_tag_ignores_case(self) -> None:
        task = LocalTask(id='LOCAL-1', summary='s', tags=['Repo:A'])
        self.assertTrue(task.has_tag('repo:a'))
        self.assertFalse(task.has_tag('repo:b'))


if __name__ == '__main__':
    unittest.main()
