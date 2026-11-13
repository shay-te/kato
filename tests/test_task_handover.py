"""Hand a task over to a teammate — and the two shared pieces it is built from.

Real ``TaskDataAccess``, ``LocalAwareTaskService``, ``LocalAwareTaskStateService``
(the classes kato wires) and a real ``LocalTaskStore`` on disk, the real
``TaskMoves`` and the real pickup gate (``active_execution_blocking_comment``);
only the tracker's HTTP client is an in-memory board that keeps each ticket's
assignee, state, tags and comments and records every call, and a live chat is a
plain stand-in with the attributes a session has. No mocks.

``SingleOwnerGateTests`` reads the source: assigning a ticket, deleting a
comment and listing who a ticket can go to each have ONE home.
"""

from __future__ import annotations

import ast
import logging
import tempfile
import types
import unittest
from pathlib import Path

from kato_core_lib.data_layers.data.fields import TaskTags
from kato_core_lib.data_layers.data_access.task_data_access import TaskDataAccess
from kato_core_lib.data_layers.service.agent_service import AgentService
from kato_core_lib.data_layers.service.local_task_routing import (
    LocalAwareTaskService,
    LocalAwareTaskStateService,
    NoTrackerClient,
)
from kato_core_lib.data_layers.service.pickup_block import (
    PickupUnblock,
    clear_pickup_block,
    pickup_blocking_comment,
)
from kato_core_lib.data_layers.service.task_handover import (
    HANDED_OVER_COMMENT,
    LOCAL_TASK_REASON,
    STEP_ASSIGN,
    STEP_COMMENTS,
    STEP_NOTE,
    STEP_OPEN,
    STEP_STOP,
    STEP_TAG,
    HandoverUnavailableError,
    TaskBusyError,
    TaskHandover,
)
from kato_core_lib.data_layers.service.task_service import TaskService
from kato_core_lib.data_layers.service.tracker_assignment import assign_verified
from local_task_core_lib.local_task_core_lib.store import LocalTaskStore
from provider_client_base.provider_client_base.data.subtask import IssueComment, IssueStatus
from provider_client_base.provider_client_base.data.tracker_user import TrackerUser

OPEN, PROGRESS, REVIEW, DONE = 'Open', 'In Progress', 'Pull Request', 'Done'
ME = 'kato-bot'
STARTED = 'Kato agent started working on this task'
STOPPED = 'Kato agent stopped working on this task: the run failed'
REPO_ROOT = Path(__file__).resolve().parent.parent


class _Board(object):
    """A tracker in memory that can hand an issue over. Every call is recorded."""

    provider_name = 'youtrack'
    supports_tags = True
    supports_subtasks = True
    supports_handover = True

    def __init__(self) -> None:
        self.assignee: dict[str, str] = {'UNA-1': ME}
        self.state: dict[str, str] = {'UNA-1': PROGRESS}
        self.tags: dict[str, list[str]] = {'UNA-1': ['kato:repo:api']}
        self.comments: dict[str, list[IssueComment]] = {'UNA-1': []}
        self.calls: list[tuple] = []
        self.refuse: dict[str, str] = {}
        self.ignore_assign = False
        self.me = ME
        self._next = 0

    def say(self, issue_id: str, body: str, *, author: str = ME) -> str:
        self._next += 1
        comment_id = f'c{self._next}'
        self.comments.setdefault(issue_id, []).insert(0, IssueComment(comment_id, author, author.title(), body))
        return comment_id

    def _call(self, name: str, *args) -> None:
        self.calls.append((name, *args))
        if name in self.refuse:
            raise RuntimeError(self.refuse[name])

    # ----- what TaskDataAccess calls -----

    def move_issue_to_state(self, issue_id, field, state):
        self._call('move', issue_id, state)
        self.state[issue_id] = state

    def add_comment(self, issue_id, comment):
        self._call('comment', issue_id)
        self.say(issue_id, comment)

    def add_tag(self, issue_id, tag):
        self._call('tag', issue_id, tag)
        self.tags.setdefault(issue_id, []).append(tag)

    # ----- the hand-over contract -----

    def list_assignable_users(self, issue_id):
        self._call('users', issue_id)
        return [TrackerUser('zed', 'Zed Last'), TrackerUser('jane', 'Jane Doe')]

    def assign_issue(self, issue_id, assignee):
        self._call('assign', issue_id, assignee)
        if not self.ignore_assign:
            self.assignee[issue_id] = assignee

    def get_issues_status(self, issue_ids, state_field):
        self._call('status', tuple(issue_ids))
        return {
            issue_id: (IssueStatus(issue_id, state=self.state[issue_id], assignee=self.assignee.get(issue_id, ''))
                       if issue_id in self.state else IssueStatus(issue_id, exists=False))
            for issue_id in issue_ids
        }

    def list_comments(self, issue_id):
        self._call('list_comments', issue_id)
        return list(self.comments.get(issue_id, []))

    def delete_comment(self, issue_id, comment_id):
        self._call('delete', issue_id, comment_id)
        self.comments[issue_id] = [c for c in self.comments[issue_id] if c.comment_id != comment_id]

    def current_user(self):
        self._call('current_user')
        return self.me


class _Chat(object):
    def __init__(self, *, working=False) -> None:
        self.is_alive = True
        self.is_working = working

    def pending_control_request_tool(self):
        return ''


class _Chats(object):
    def __init__(self, chats=None) -> None:
        self._chats = dict(chats or {})

    def get_session(self, task_id):
        return self._chats.get(task_id)


class _Stopper(object):
    def __init__(self, *, fails: str = '') -> None:
        self.stopped: list[str] = []
        self.fails = fails

    def __call__(self, task_id):
        if self.fails:
            raise RuntimeError(self.fails)
        self.stopped.append(task_id)
        return 'stopped'


def _config():
    return types.SimpleNamespace(
        project='PROJ', assignee=ME, issue_states=[OPEN],
        open_state=OPEN, progress_state=PROGRESS, review_state=REVIEW, done_state=DONE,
        progress_state_field='State', review_state_field='State',
        done_state_field='State', open_state_field='State',
    )


def _gate_clear(board: _Board, issue_id: str = 'UNA-1') -> bool:
    """What every kato's pickup reads: does anything still block the task?"""
    return not pickup_blocking_comment(board.comments.get(issue_id, []))


class _Setup(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = LocalTaskStore(Path(tmp.name) / 'local_tasks.json')
        self.board = _Board()
        self.logger = logging.getLogger(f'test_task_handover.{self.id()}')
        self.chats = _Chats()
        self.handover = self._handover(self.board)
        self.stopper = _Stopper()

    def _services(self, client):
        access = TaskDataAccess(_config(), client)
        return (LocalAwareTaskService(_config(), access, self.store),
                LocalAwareTaskStateService(_config(), access, self.store))

    def _handover(self, client, *, signed_by=lambda: "Shay's kato") -> TaskHandover:
        tasks, states = self._services(client)
        return TaskHandover(
            task_service=tasks, task_state_service=states, session_manager=self.chats,
            signed_by=signed_by, logger=self.logger,
        )

    def hand(self, handle='jane', name='Jane Doe', *, quiet=True):
        if not quiet:
            return self.handover.hand_over('UNA-1', handle, name, stop_agent=self.stopper)
        logging.disable(logging.CRITICAL)
        try:
            return self.handover.hand_over('UNA-1', handle, name, stop_agent=self.stopper)
        finally:
            logging.disable(logging.NOTSET)

    @staticmethod
    def steps(result) -> dict[str, tuple[bool, str]]:
        return {step['step']: (step['ok'], step['detail']) for step in result['steps']}


class AvailabilityTests(_Setup):
    def test_a_tracker_ticket_can_be_handed_over(self) -> None:
        self.assertEqual(self.handover.unavailable_reason('UNA-1'), '')

    def test_a_local_task_cannot_no_one_else_s_kato_sees_it(self) -> None:
        local_id = self.store.create('local', 'details').id
        self.assertEqual(self.handover.unavailable_reason(local_id), LOCAL_TASK_REASON)
        with self.assertRaisesRegex(HandoverUnavailableError, 'lives only here'):
            self.handover.users(local_id)

    def test_an_install_without_a_tracker_cannot(self) -> None:
        handover = self._handover(NoTrackerClient())
        self.assertIn('(local) cannot hand a ticket over', handover.unavailable_reason('UNA-1'))

    def test_a_tracker_that_cannot_hand_over_says_which(self) -> None:
        for flags in ({'supports_handover': False}, {'supports_subtasks': False}):
            with self.subTest(flags=flags):
                board = _Board()
                for key, value in flags.items():
                    setattr(board, key, value)
                board.provider_name = 'bitbucket'
                tasks = TaskService(_config(), TaskDataAccess(_config(), board))
                handover = TaskHandover(task_service=tasks, task_state_service=None, session_manager=None)
                self.assertIn('(bitbucket) cannot hand a ticket over', handover.unavailable_reason('UNA-1'))

    def test_a_service_without_the_accessors_reads_as_unavailable(self) -> None:
        handover = TaskHandover(task_service=object(), task_state_service=None, session_manager=None)
        self.assertIn('(this one)', handover.unavailable_reason('UNA-1'))

    def test_users_are_the_tracker_s_by_handle_and_name(self) -> None:
        self.assertEqual(self.handover.users('UNA-1'), [
            {'handle': 'zed', 'name': 'Zed Last'}, {'handle': 'jane', 'name': 'Jane Doe'},
        ])


class HandOverTests(_Setup):
    def setUp(self) -> None:
        super().setUp()
        self.board.say('UNA-1', 'please look at the date picker too', author='jane')
        self.board.say('UNA-1', STARTED)
        self.board.say('UNA-1', STOPPED)

    def test_end_to_end_their_kato_can_take_it(self) -> None:
        result = self.hand(quiet=False)
        self.assertEqual(result['status'], 'handed_over')
        self.assertTrue(result['landed'])
        self.assertEqual(result['assignee'], {'handle': 'jane', 'name': 'Jane Doe'})
        self.assertEqual(self.board.assignee['UNA-1'], 'jane')
        self.assertIn(TaskTags.WAIT_EDITING, self.board.tags['UNA-1'])
        self.assertEqual(self.board.state['UNA-1'], OPEN)
        bodies = [comment.body for comment in self.board.comments['UNA-1']]
        self.assertNotIn(STARTED, bodies)
        self.assertNotIn(STOPPED, bodies)
        self.assertIn('please look at the date picker too', bodies)  # a person's comment stays
        self.assertEqual(bodies[0], HANDED_OVER_COMMENT.format(name='Jane Doe', by="Shay's kato"))
        self.assertTrue(_gate_clear(self.board))
        self.assertEqual(self.stopper.stopped, ['UNA-1'])
        self.assertEqual(self.steps(result), {
            STEP_ASSIGN: (True, ''), STEP_TAG: (True, ''),
            STEP_COMMENTS: (True, "deleted 2 of kato's comments"),
            STEP_NOTE: (True, ''), STEP_OPEN: (True, ''), STEP_STOP: (True, 'stopped'),
        })

    def test_open_is_the_last_ticket_write_so_their_kato_only_sees_it_finished(self) -> None:
        self.hand()
        writes = [call[0] for call in self.board.calls if call[0] in ('assign', 'tag', 'delete', 'comment', 'move')]
        self.assertEqual(writes, ['assign', 'tag', 'delete', 'delete', 'comment', 'move'])

    def test_a_comment_worded_like_kato_but_written_by_a_person_is_kept_and_the_note_clears_it(self) -> None:
        self.board.say('UNA-1', STARTED, author='jane')  # shared wording, someone else's account
        self.hand()
        self.assertIn(STARTED, [comment.body for comment in self.board.comments['UNA-1']])
        self.assertTrue(_gate_clear(self.board))  # the override note after it clears it

    def test_one_deleted_comment_reads_singular(self) -> None:
        self.board.comments['UNA-1'] = [c for c in self.board.comments['UNA-1'] if c.body != STOPPED]
        self.assertEqual(self.steps(self.hand())[STEP_COMMENTS], (True, "deleted 1 of kato's comment"))

    def test_nothing_of_kato_s_blocking_asks_the_tracker_nothing_more(self) -> None:
        self.board.comments['UNA-1'] = []
        result = self.hand()
        self.assertEqual(self.steps(result)[STEP_COMMENTS], (True, "nothing of kato's blocked it"))
        self.assertNotIn(('current_user',), self.board.calls)

    def test_a_refused_assignment_does_nothing_else(self) -> None:
        self.board.refuse['assign'] = 'Unknown user: jane'
        result = self.hand()
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(result['landed'])
        self.assertEqual(self.steps(result), {STEP_ASSIGN: (False, 'Unknown user: jane')})
        self.assertEqual((self.board.assignee['UNA-1'], self.board.state['UNA-1']), (ME, PROGRESS))
        self.assertNotIn(TaskTags.WAIT_EDITING, self.board.tags['UNA-1'])
        self.assertEqual(self.stopper.stopped, [])

    def test_an_assignment_the_tracker_ignored_is_caught_by_reading_it_back(self) -> None:
        self.board.ignore_assign = True
        result = self.hand()
        self.assertEqual(self.steps(result)[STEP_ASSIGN],
                         (False, 'UNA-1 was not assigned to jane: the tracker shows kato-bot'))
        self.assertEqual(result['status'], 'failed')

    def test_a_failed_tag_stops_before_their_kato_could_run_it_unwatched(self) -> None:
        self.board.refuse['tag'] = 'tag store down'
        result = self.hand()
        self.assertEqual(result['status'], 'handed_over_with_problems')
        self.assertFalse(result['landed'])  # assigned to them, but never in their queue
        self.assertEqual(list(self.steps(result)), [STEP_ASSIGN, STEP_TAG])
        self.assertEqual(self.board.state['UNA-1'], PROGRESS)  # never released to their queue
        self.assertEqual(self.stopper.stopped, [])

    def test_a_refused_delete_is_reported_and_the_note_still_clears_the_block(self) -> None:
        self.board.refuse['delete'] = 'not allowed'
        result = self.hand()
        ok, detail = self.steps(result)[STEP_COMMENTS]
        self.assertFalse(ok)
        self.assertIn("could not delete kato's comment c3: not allowed", detail)
        self.assertEqual(result['status'], 'handed_over_with_problems')
        self.assertTrue(result['landed'])  # the note clears the block: it is theirs
        self.assertEqual(self.board.state['UNA-1'], OPEN)
        self.assertTrue(_gate_clear(self.board))

    def test_an_account_kato_cannot_name_deletes_nothing(self) -> None:
        self.board.refuse['current_user'] = 'token revoked'
        result = self.hand()
        self.assertEqual(self.steps(result)[STEP_COMMENTS],
                         (False, "could not tell which comments are kato's own: token revoked"))
        self.assertTrue(_gate_clear(self.board))

    def test_a_refused_move_to_open_still_stops_the_agent(self) -> None:
        self.board.refuse['move'] = 'workflow forbids'
        result = self.hand()
        self.assertEqual(self.steps(result)[STEP_OPEN], (False, 'workflow forbids'))
        self.assertFalse(result['landed'])  # not in Open: their kato's queue never shows it
        self.assertEqual(self.stopper.stopped, ['UNA-1'])

    def test_a_failed_stop_is_reported(self) -> None:
        self.stopper.fails = 'process would not die'
        result = self.hand()
        self.assertEqual(self.steps(result)[STEP_STOP], (False, 'process would not die'))
        self.assertTrue(result['landed'])  # this kato's own Stop does not decide whose it is

    def test_refused_while_the_agent_is_working(self) -> None:
        self.chats._chats['UNA-1'] = _Chat(working=True)
        with self.assertRaisesRegex(TaskBusyError, "UNA-1's agent is working"):
            self.hand()
        self.assertEqual(self.board.calls, [])

    def test_no_one_picked(self) -> None:
        with self.assertRaisesRegex(ValueError, 'pick who'):
            self.hand(handle='  ')

    def test_a_local_task_is_refused(self) -> None:
        local_id = self.store.create('local', 'details').id
        with self.assertRaises(HandoverUnavailableError):
            self.handover.hand_over(local_id, 'jane', stop_agent=self.stopper)

    def test_no_name_signs_with_the_handle_and_no_agent_name_signs_kato(self) -> None:
        self.handover = self._handover(self.board, signed_by=lambda: '')
        self.hand(name='')
        self.assertEqual(self.board.comments['UNA-1'][0].body,
                         HANDED_OVER_COMMENT.format(name='jane', by='kato'))


class TrackerAssignmentTests(unittest.TestCase):
    def test_assigns_and_reads_it_back_ignoring_case(self) -> None:
        board = _Board()
        assign_verified(board, 'UNA-1', 'Jane')
        self.assertEqual(board.assignee['UNA-1'], 'Jane')

    def test_a_ticket_gone_after_the_assignment_raises(self) -> None:
        board = _Board()
        board.get_issues_status = lambda ids, field: {'UNA-1': IssueStatus('UNA-1', exists=False)}
        with self.assertRaisesRegex(RuntimeError, 'no longer on the tracker'):
            assign_verified(board, 'UNA-1', 'jane')

    def test_a_status_read_without_the_ticket_raises(self) -> None:
        board = _Board()
        board.get_issues_status = lambda ids, field: {}
        with self.assertRaisesRegex(RuntimeError, 'no longer on the tracker'):
            assign_verified(board, 'UNA-1', 'jane')

    def test_left_unassigned_says_nobody(self) -> None:
        board = _Board()
        board.assignee['UNA-1'] = ''
        board.ignore_assign = True
        with self.assertRaisesRegex(RuntimeError, 'the tracker shows nobody'):
            assign_verified(board, 'UNA-1', 'jane')


class PickupBlockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.board = _Board()
        self.posted: list[tuple[str, str]] = []
        self.task_service = types.SimpleNamespace(add_comment=lambda task, text: self.posted.append((task, text)))

    def test_nothing_blocking_touches_nothing(self) -> None:
        self.board.say('UNA-1', 'a person writing')
        result = clear_pickup_block(self.board, self.task_service, 'UNA-1', delete_own=True, note='kato: retry approved')
        self.assertEqual(result, PickupUnblock())
        self.assertEqual([call[0] for call in self.board.calls], ['list_comments'])

    def test_the_plan_queue_way_notes_without_deleting(self) -> None:
        self.board.say('UNA-1', STARTED)
        result = clear_pickup_block(self.board, self.task_service, 'UNA-1', delete_own=False,
                                    note='kato: retry approved — released again.')
        self.assertEqual(result, PickupUnblock(noted=True))
        self.assertEqual(self.posted, [('UNA-1', 'kato: retry approved — released again.')])
        self.assertEqual(len(self.board.comments['UNA-1']), 1)

    def test_everything_deleted_needs_no_note(self) -> None:
        self.board.say('UNA-1', STARTED)
        result = clear_pickup_block(self.board, self.task_service, 'UNA-1', delete_own=True, note='kato: retry approved')
        self.assertEqual((result.deleted, result.noted), (('c1',), False))
        self.assertEqual(self.posted, [])

    def test_still_blocked_without_a_note_is_left_as_it_is(self) -> None:
        self.board.say('UNA-1', STARTED, author='jane')
        result = clear_pickup_block(self.board, self.task_service, 'UNA-1', delete_own=True)
        self.assertEqual(result, PickupUnblock())
        self.assertEqual(self.posted, [])

    def test_an_account_with_no_name_deletes_nothing(self) -> None:
        self.board.say('UNA-1', STARTED)
        self.board.me = ''
        result = clear_pickup_block(self.board, self.task_service, 'UNA-1', delete_own=True)
        self.assertEqual(result.problems, ("could not tell which comments are kato's own: the tracker named no account",))

    def test_only_blocking_comments_of_the_account_go(self) -> None:
        self.board.say('UNA-1', 'Kato addressed review comment 7')  # kato's, but not blocking
        self.board.say('UNA-1', STOPPED)
        clear_pickup_block(self.board, self.task_service, 'UNA-1', delete_own=True)
        self.assertEqual([c.body for c in self.board.comments['UNA-1']], ['Kato addressed review comment 7'])


class AgentServiceHandoverTests(unittest.TestCase):
    def test_the_agent_service_hands_over_with_its_own_services(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = LocalTaskStore(Path(tmp.name) / 'local.json')
        access = TaskDataAccess(_config(), _Board())
        tasks = LocalAwareTaskService(_config(), access, store)
        states = LocalAwareTaskStateService(_config(), access, store)
        unused = types.SimpleNamespace()
        agent = AgentService(
            task_service=tasks, task_state_service=states, implementation_service=unused,
            testing_service=unused, repository_service=unused, notification_service=unused,
        )
        handover = agent.handover
        self.assertIsInstance(handover, TaskHandover)
        self.assertEqual(handover.unavailable_reason('UNA-1'), '')


#: Each tracker call that changes who has a ticket, and its ONE home.
_OWNED_CALLS = {
    'assign_issue': 'kato_core_lib/data_layers/service/tracker_assignment.py',
    'delete_comment': 'kato_core_lib/data_layers/service/pickup_block.py',
    'list_assignable_users': 'kato_core_lib/data_layers/service/task_handover.py',
}
_SCANNED = ('kato_core_lib', 'webserver/kato_webserver')


def _calls_outside_their_home() -> list[str]:
    found = []
    for root in _SCANNED:
        for path in sorted((REPO_ROOT / root).rglob('*.py')):
            relative = path.relative_to(REPO_ROOT).as_posix()
            if '/tests/' in f'/{relative}':
                continue
            tree = ast.parse(path.read_text(encoding='utf-8'), filename=relative)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    home = _OWNED_CALLS.get(node.func.attr)
                    if home and relative != home:
                        found.append(f'{relative}:{node.lineno} {node.func.attr}')
    return found


class SingleOwnerGateTests(unittest.TestCase):
    def test_assigning_deleting_and_listing_people_each_have_one_home(self) -> None:
        self.assertEqual(_calls_outside_their_home(), [],
                         'go through tracker_assignment / pickup_block / task_handover')

    def test_the_homes_are_real_files(self) -> None:
        for home in _OWNED_CALLS.values():
            self.assertTrue((REPO_ROOT / home).is_file(), home)


if __name__ == '__main__':
    unittest.main()
