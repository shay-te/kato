"""Local tasks ride the tracker's services: by id, in every list, never on the tracker.

Real ``LocalTaskStore`` on disk and the real TaskService / TaskStateService
subclasses; only the ticket platform's HTTP client is an in-memory stand-in,
which records every call so the tests can prove a LOCAL task never reaches it.
"""

from __future__ import annotations

import tempfile
import types
import unittest
from pathlib import Path

from kato_core_lib.data_layers.data.task import Task
from kato_core_lib.data_layers.data_access.task_data_access import TaskDataAccess
from kato_core_lib.data_layers.service.local_task_routing import (
    LocalAwareTaskService,
    LocalAwareTaskStateService,
    local_task_as_task,
)
from kato_core_lib.helpers.agent_comment_classification import (
    active_execution_blocking_comment,
)
from kato_core_lib.helpers.task_lookup_utils import find_assigned_or_review_task
from local_task_core_lib.local_task_core_lib.data.local_task import LocalTaskState
from local_task_core_lib.local_task_core_lib.store import LocalTaskStore

PROGRESS, REVIEW, DONE = 'In Progress', 'To Verify', 'Done'


class _Tracker(object):
    """A ticket platform in memory. Tickets have a state; every call is logged."""

    def __init__(self, tickets: dict[str, str]) -> None:
        self.states = dict(tickets)
        self.calls: list[tuple] = []

    def get_assigned_tasks(self, project, assignee, states):
        self.calls.append(('list', tuple(states)))
        return [Task(id=task_id, summary=task_id) for task_id, state in self.states.items()
                if state in states]

    def get_task(self, issue_id):
        self.calls.append(('get', issue_id))
        return Task(id=issue_id) if issue_id in self.states else None

    def add_comment(self, issue_id, comment):
        self.calls.append(('comment', issue_id))

    def add_tag(self, issue_id, tag):
        self.calls.append(('tag', issue_id))

    def remove_tag(self, issue_id, tag):
        self.calls.append(('untag', issue_id))

    def move_issue_to_state(self, issue_id, field, state):
        self.calls.append(('move', issue_id, state))

    def download_image_attachments(self, issue_id, destination_dir):
        self.calls.append(('images', issue_id))
        return ['shot.png']

    def touched(self, issue_id: str) -> list[tuple]:
        return [call for call in self.calls if len(call) > 1 and call[1] == issue_id]


class _Routing(unittest.TestCase):

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = LocalTaskStore(Path(tmp.name) / 'local_tasks.json')
        self.config = types.SimpleNamespace(
            project='PROJ', assignee='me', issue_states=['Todo', 'Open'],
            progress_state=PROGRESS, review_state=REVIEW, done_state=DONE,
            progress_state_field='State', review_state_field='State',
            done_state_field='State', open_state_field='State',
        )
        self.tracker = _Tracker({'UNA-1': 'Todo', 'UNA-2': PROGRESS, 'UNA-3': REVIEW})
        access = TaskDataAccess(self.config, self.tracker)
        self.tasks = LocalAwareTaskService(self.config, access, self.store)
        self.states = LocalAwareTaskStateService(self.config, access, self.store)

    def local(self, state: LocalTaskState, summary: str = 'local') -> str:
        return self.store.create(summary, 'details', tags=['kato:repo:api'], state=state).id

    @staticmethod
    def ids(tasks) -> list[str]:
        return [task.id for task in tasks]


class ListTests(_Routing):

    def test_every_list_carries_the_local_tasks_in_its_state(self) -> None:
        opened = self.local(LocalTaskState.OPEN)
        working = self.local(LocalTaskState.IN_PROGRESS)
        reviewing = self.local(LocalTaskState.REVIEW)
        done = self.local(LocalTaskState.DONE)

        self.assertEqual(self.ids(self.tasks.get_assigned_tasks()), ['UNA-1', opened])
        self.assertEqual(self.ids(self.tasks.get_started_tasks()),
                         ['UNA-2', 'UNA-3', working, reviewing])
        self.assertEqual(self.ids(self.tasks.get_review_tasks()), ['UNA-3', reviewing])
        self.assertEqual(
            sorted(self.ids(self.tasks.list_all_assigned_tasks())),
            sorted(['UNA-1', 'UNA-2', 'UNA-3', opened, working, reviewing, done]),
        )

    def test_state_names_match_ignoring_case_and_punctuation(self) -> None:
        reviewing = self.local(LocalTaskState.REVIEW)
        found = self.tasks.get_assigned_tasks(states=['to-verify'])
        self.assertEqual(self.ids(found), [reviewing])

    def test_an_unreadable_store_still_lists_the_tracker(self) -> None:
        self.store.path.write_text('not json', encoding='utf-8')
        with self.assertLogs(self.tasks.logger, level='ERROR'):
            self.assertEqual(self.ids(self.tasks.get_assigned_tasks()), ['UNA-1'])

    def test_lookup_by_id_finds_a_local_task_like_a_ticket(self) -> None:
        working = self.local(LocalTaskState.IN_PROGRESS, summary='Add retry')
        task = find_assigned_or_review_task(self.tasks, working)
        self.assertEqual((task.id, task.summary, task.tags), (working, 'Add retry', ['kato:repo:api']))


class ByIdTests(_Routing):

    def test_a_local_task_never_reaches_the_tracker(self) -> None:
        task_id = self.local(LocalTaskState.IN_PROGRESS)
        self.assertEqual(self.tasks.get_task(task_id).summary, 'local')
        self.tasks.add_comment(task_id, 'Work started.')
        self.tasks.add_pull_request_comment(task_id, 'https://git/pr/1')
        self.tasks.add_tag(task_id, 'kato:repo:web')
        self.tasks.remove_tag(task_id, 'kato:repo:api')
        self.assertEqual(self.tasks.download_image_attachments(task_id, '/tmp/x'), [])
        self.states.move_task_to_review(task_id)

        stored = self.store.get(task_id)
        self.assertEqual([c.body for c in stored.comments],
                         ['Work started.', 'Pull request created: https://git/pr/1'])
        self.assertEqual(stored.tags, ['kato:repo:web'])
        self.assertEqual(stored.state, LocalTaskState.REVIEW)
        self.assertEqual(self.tracker.touched(task_id), [])

    def test_a_deleted_local_task_reads_as_none(self) -> None:
        task_id = self.local(LocalTaskState.OPEN)
        self.store.delete(task_id)
        self.assertIsNone(self.tasks.get_task(task_id))
        self.assertEqual(self.tracker.touched(task_id), [])

    def test_a_ticket_still_goes_to_the_tracker(self) -> None:
        self.assertEqual(self.tasks.get_task('UNA-1').id, 'UNA-1')
        self.tasks.add_comment('UNA-1', 'hi')
        self.tasks.add_pull_request_comment('UNA-1', 'https://git/pr/2')
        self.tasks.add_tag('UNA-1', 't')
        self.tasks.remove_tag('UNA-1', 't')
        self.assertEqual(self.tasks.download_image_attachments('UNA-1', '/tmp/x'), ['shot.png'])
        for move in (self.states.move_task_to_in_progress, self.states.move_task_to_review,
                     self.states.move_task_to_done, self.states.move_task_to_open):
            move('UNA-1')
        kinds = [call[0] for call in self.tracker.touched('UNA-1')]
        self.assertEqual(kinds, ['get', 'comment', 'comment', 'tag', 'untag', 'images',
                                 'move', 'move', 'move', 'move'])

    def test_every_state_move_lands_in_the_store(self) -> None:
        task_id = self.local(LocalTaskState.OPEN)
        for move, state in (
            (self.states.move_task_to_in_progress, LocalTaskState.IN_PROGRESS),
            (self.states.move_task_to_review, LocalTaskState.REVIEW),
            (self.states.move_task_to_done, LocalTaskState.DONE),
            (self.states.move_task_to_open, LocalTaskState.OPEN),
        ):
            move(task_id)
            self.assertEqual(self.store.get(task_id).state, state)
        self.assertEqual(self.tracker.touched(task_id), [])

    def test_a_failure_comment_blocks_a_rerun_as_on_a_ticket(self) -> None:
        # The failure handler comments, then moves the task back to open; the
        # scan must then see the block, or a failed local task reruns every tick.
        task_id = self.local(LocalTaskState.IN_PROGRESS)
        self.tasks.add_comment(task_id, 'Kato agent could not safely process this task: boom')
        self.states.move_task_to_open(task_id)
        queued = [t for t in self.tasks.get_assigned_tasks() if t.id == task_id]
        self.assertEqual(len(queued), 1)
        self.assertTrue(active_execution_blocking_comment(queued[0].all_comments))
        self.assertEqual(self.tasks.local_tasks, self.store)
        self.assertTrue(self.tasks.is_local(task_id))
        self.assertFalse(self.tasks.is_local('UNA-1'))


class ConversionTests(unittest.TestCase):

    def test_comments_and_state_read_like_a_ticket(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = LocalTaskStore(Path(tmp.name) / 't.json')
        local = store.create('s', 'd', tags=['a'], state=LocalTaskState.REVIEW)
        local = store.add_comment(local.id, 'hello', author='kato', author_id='kato')
        task = local_task_as_task(local)
        self.assertEqual((task.id, task.summary, task.description, task.tags),
                         (local.id, 's', 'd', ['a']))
        self.assertEqual(task.state, 'In Review')
        self.assertEqual(task.all_comments,
                         [{'author': 'kato', 'author_id': 'kato', 'body': 'hello'}])


if __name__ == '__main__':
    unittest.main()
