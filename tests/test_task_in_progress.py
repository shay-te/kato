"""A task kato works on reaches In Progress on its ticket — through ONE mover.

Real ``LocalAwareTaskStateService`` (the class kato wires), real
``LocalTaskStore`` on disk, real ``TaskDataAccess``; only the tracker's HTTP
client is an in-memory board that keeps each ticket's state, and the live chat
is a plain stand-in with the three attributes a session has. No mocks.
"""

from __future__ import annotations

import logging
import tempfile
import types
import unittest
from pathlib import Path

from kato_core_lib.data_layers.data.task import Task
from kato_core_lib.data_layers.data_access.task_data_access import TaskDataAccess
from kato_core_lib.data_layers.service.agent_service import AgentService
from kato_core_lib.data_layers.service.local_task_routing import (
    LocalAwareTaskStateService,
    NoTrackerClient,
)
from kato_core_lib.data_layers.service.restart_resume.running_agents import (
    is_mid_turn,
    running_task_ids,
)
from kato_core_lib.data_layers.service.task_in_progress import (
    AGENT_IS_WORKING,
    CHAT_OPENED_FOR_IT,
    KATO_STARTED_IT,
    TaskInProgress,
)
from kato_core_lib.data_layers.service.wait_planning_service import WaitPlanningService
from local_task_core_lib.local_task_core_lib.data.local_task import LocalTaskState
from local_task_core_lib.local_task_core_lib.store import LocalTaskStore
from tests.utils import build_task

OPEN, PROGRESS, REVIEW, DONE = 'Open', 'In Progress', 'Pull Request', 'Done'


class _Board(object):
    """A tracker in memory: each ticket has a state, and a move changes it."""

    def __init__(self, tickets: dict[str, str], *, refuses: str = '') -> None:
        self.states = dict(tickets)
        self.refuses = refuses

    def move_issue_to_state(self, issue_id, field, state):
        if self.refuses:
            raise RuntimeError(self.refuses)
        self.states[issue_id] = state


class _Chat(object):
    def __init__(self, *, alive=True, working=False, asking='') -> None:
        self.is_alive = alive
        self.is_working = working
        self._asking = asking

    def pending_control_request_tool(self):
        return self._asking


class _Chats(object):
    """What the session manager answers: the task's live chat, if any."""

    def __init__(self, **chats: _Chat) -> None:
        self._chats = {task.replace('_', '-'): chat for task, chat in chats.items()}

    def get_session(self, task_id):
        return self._chats.get(task_id)

    def list_records(self):
        return [types.SimpleNamespace(task_id=task_id) for task_id in self._chats]


class _Unreadable(object):
    def get_session(self, task_id):
        raise OSError('the session registry is unreadable')

    def list_records(self):
        raise OSError('the session registry is unreadable')


class _MustNotRun(object):
    """A collaborator the flow under test must never reach."""

    def __init__(self, name: str) -> None:
        self._name = name

    def __getattr__(self, attribute):
        raise AssertionError(f'{self._name}.{attribute} was used')


def _config():
    return types.SimpleNamespace(
        project='PROJ', assignee='me', issue_states=[OPEN],
        open_state=OPEN, progress_state=PROGRESS, review_state=REVIEW, done_state=DONE,
        progress_state_field='State', review_state_field='State',
        done_state_field='State', open_state_field='State',
    )


class _Setup(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = LocalTaskStore(Path(tmp.name) / 'local_tasks.json')
        self.board = _Board({'UNA-1': OPEN, 'UNA-2': OPEN})
        self.states = self._states(self.board)
        self.logger = logging.getLogger(f'test_task_in_progress.{self.id()}')
        self.mover = TaskInProgress(self.states, logger=self.logger)

    def _states(self, client):
        return LocalAwareTaskStateService(_config(), TaskDataAccess(_config(), client), self.store)

    def local_task(self, state=LocalTaskState.OPEN) -> str:
        return self.store.create('local', 'details', state=state).id


class MoveTests(_Setup):
    def test_a_tracker_ticket_moves_to_in_progress_and_says_why(self) -> None:
        with self.assertLogs(self.logger, level='INFO') as logs:
            self.mover.move('UNA-1', KATO_STARTED_IT)
        self.assertEqual(self.board.states['UNA-1'], PROGRESS)
        self.assertEqual(self.board.states['UNA-2'], OPEN)
        self.assertIn('task UNA-1 moved to in progress: kato started working on it', logs.output[0])

    def test_a_local_task_moves_in_kato_s_own_store_never_on_the_tracker(self) -> None:
        task_id = self.local_task()
        self.mover.move(task_id, KATO_STARTED_IT)
        self.assertEqual(self.store.get(task_id).state, LocalTaskState.IN_PROGRESS)
        self.assertEqual(self.board.states, {'UNA-1': OPEN, 'UNA-2': OPEN})

    def test_a_refusing_tracker_is_raised_to_the_caller(self) -> None:
        mover = TaskInProgress(self._states(_Board({'UNA-1': OPEN}, refuses='no such state')))
        with self.assertRaisesRegex(RuntimeError, 'no such state'):
            mover.move('UNA-1', KATO_STARTED_IT)

    def test_no_tracker_refuses_a_ticket_but_moves_a_local_task(self) -> None:
        mover = TaskInProgress(self._states(NoTrackerClient()), logger=self.logger)
        task_id = self.local_task()
        mover.move(task_id, CHAT_OPENED_FOR_IT)
        self.assertEqual(self.store.get(task_id).state, LocalTaskState.IN_PROGRESS)
        with self.assertRaisesRegex(ValueError, 'this install has no issue tracker'):
            mover.move('UNA-1', CHAT_OPENED_FOR_IT)

    def test_a_default_logger_is_made_when_none_is_given(self) -> None:
        self.assertIsNotNone(TaskInProgress(self.states).logger)


class TryMoveTests(_Setup):
    def test_true_once_moved(self) -> None:
        self.assertTrue(self.mover.try_move('UNA-1', CHAT_OPENED_FOR_IT))
        self.assertEqual(self.board.states['UNA-1'], PROGRESS)

    def test_false_and_logged_when_the_tracker_refuses_never_raised(self) -> None:
        mover = TaskInProgress(
            self._states(_Board({'UNA-1': OPEN}, refuses='the workflow forbids it')),
            logger=self.logger,
        )
        with self.assertLogs(self.logger, level='ERROR') as logs:
            self.assertFalse(mover.try_move('UNA-1', CHAT_OPENED_FOR_IT))
        self.assertIn('could not move task UNA-1 to in progress (kato opened its chat)', logs.output[0])


class MoveIfAgentWorkingTests(_Setup):
    def test_a_working_agent_moves_its_ticket(self) -> None:
        chats = _Chats(UNA_1=_Chat(working=True))
        self.assertTrue(self.mover.move_if_agent_working('UNA-1', chats))
        self.assertEqual(self.board.states, {'UNA-1': PROGRESS, 'UNA-2': OPEN})

    def test_an_agent_waiting_for_the_operator_inside_its_turn_counts_as_working(self) -> None:
        chats = _Chats(UNA_1=_Chat(working=False, asking='Bash'))
        self.assertTrue(self.mover.move_if_agent_working('UNA-1', chats))
        self.assertEqual(self.board.states['UNA-1'], PROGRESS)

    def test_an_idle_chat_moves_nothing(self) -> None:
        # A run that failed moved the ticket back to Open on purpose, and its
        # chat stays alive afterwards: only WORK moves it forward again.
        chats = _Chats(UNA_1=_Chat(working=False))
        self.assertFalse(self.mover.move_if_agent_working('UNA-1', chats))
        self.assertEqual(self.board.states['UNA-1'], OPEN)

    def test_a_dead_chat_moves_nothing(self) -> None:
        chats = _Chats(UNA_1=_Chat(alive=False, working=True))
        self.assertFalse(self.mover.move_if_agent_working('UNA-1', chats))

    def test_no_chat_no_manager_or_an_unreadable_one_moves_nothing(self) -> None:
        for chats in (_Chats(), None, _Unreadable()):
            with self.subTest(chats=chats):
                self.assertFalse(self.mover.move_if_agent_working('UNA-1', chats))
        self.assertEqual(self.board.states['UNA-1'], OPEN)

    def test_working_but_the_tracker_refuses_is_still_working(self) -> None:
        mover = TaskInProgress(
            self._states(_Board({'UNA-1': OPEN}, refuses='offline')), logger=self.logger,
        )
        with self.assertLogs(self.logger, level='ERROR'):
            self.assertTrue(mover.move_if_agent_working('UNA-1', _Chats(UNA_1=_Chat(working=True))))

    def test_a_local_task_s_working_agent_moves_it_in_the_store(self) -> None:
        task_id = self.local_task()
        chats = _Chats(**{task_id.replace('-', '_'): _Chat(working=True)})
        self.assertTrue(self.mover.move_if_agent_working(task_id, chats))
        self.assertEqual(self.store.get(task_id).state, LocalTaskState.IN_PROGRESS)


class FailedRunThenContinueTests(_Setup):
    """The report: autopilot tasks kato was working on, still in Open."""

    def test_open_after_the_failed_run_in_progress_once_the_agent_works_again(self) -> None:
        self.mover.move('UNA-1', KATO_STARTED_IT)
        self.states.move_task_to_open('UNA-1')  # what TaskFailureHandler does on a failed run
        self.assertEqual(self.board.states['UNA-1'], OPEN)
        idle = _Chats(UNA_1=_Chat(working=False))
        self.assertFalse(self.mover.move_if_agent_working('UNA-1', idle))
        self.assertEqual(self.board.states['UNA-1'], OPEN)
        # The operator types "continue": the agent works again.
        working = _Chats(UNA_1=_Chat(working=True))
        self.assertTrue(self.mover.move_if_agent_working('UNA-1', working))
        self.assertEqual(self.board.states['UNA-1'], PROGRESS)


class QueueScanTests(_Setup):
    """``AgentService.process_assigned_task`` — the queue scan — uses the mover."""

    def _agent(self, chats) -> AgentService:
        return AgentService(
            task_service=_MustNotRun('task_service'),
            task_state_service=self.states,
            implementation_service=_MustNotRun('implementation_service'),
            testing_service=_MustNotRun('testing_service'),
            repository_service=_MustNotRun('repository_service'),
            notification_service=_MustNotRun('notification_service'),
            session_manager=chats,
            logger=self.logger,
        )

    def test_a_queue_ticket_whose_agent_works_is_moved_and_nothing_else_starts(self) -> None:
        agent = self._agent(_Chats(UNA_1=_Chat(working=True)))
        result = agent.process_assigned_task(Task(id='UNA-1', summary='x'))
        self.assertEqual(self.board.states['UNA-1'], PROGRESS)
        self.assertIsNotNone(result)  # a skip: no implementation, no testing, no publish

    def test_kato_s_own_start_moves_through_the_same_mover(self) -> None:
        agent = self._agent(None)
        prepared = types.SimpleNamespace(repositories=[])
        agent._task_publisher = types.SimpleNamespace(comment_task_started=lambda *a: None)
        with self.assertLogs(self.logger, level='INFO') as logs:
            self.assertTrue(agent._start_task_processing(Task(id='UNA-2'), prepared))
        self.assertEqual(self.board.states['UNA-2'], PROGRESS)
        self.assertTrue(any(KATO_STARTED_IT in line for line in logs.output))


class PlanningChatTests(_Setup):
    """``WaitPlanningService`` moves through the same mover, FIRST."""

    def _planning(self, chats) -> WaitPlanningService:
        service = WaitPlanningService(
            session_manager=chats,
            repository_service=_MustNotRun('repository_service'),
            task_state_service=self.states,
        )
        service.logger = self.logger
        return service

    def test_a_planning_ticket_whose_chat_already_runs_is_moved(self) -> None:
        planning = self._planning(_Chats(UNA_1=_Chat(working=False)))
        with self.assertLogs(self.logger, level='INFO') as logs:
            self.assertIsNotNone(
                planning.handle_task(build_task(task_id='UNA-1', tags=['kato:wait-planning'])),
            )
        self.assertEqual(self.board.states['UNA-1'], PROGRESS)
        self.assertTrue(any(CHAT_OPENED_FOR_IT in line for line in logs.output))


class RunningAgentsTests(unittest.TestCase):
    def test_is_mid_turn_without_a_manager_is_false(self) -> None:
        self.assertFalse(is_mid_turn(None, 'UNA-1'))

    def test_running_task_ids_lists_working_and_asking_chats_only(self) -> None:
        chats = _Chats(
            UNA_1=_Chat(working=True), UNA_2=_Chat(asking='Edit'), UNA_3=_Chat(),
            UNA_4=_Chat(alive=False, working=True),
        )
        self.assertEqual(running_task_ids(chats), ['UNA-1', 'UNA-2'])

    def test_running_task_ids_without_a_manager_or_an_unreadable_one_is_empty(self) -> None:
        self.assertEqual(running_task_ids(None), [])
        self.assertEqual(running_task_ids(_Unreadable()), [])

    def test_why_words_are_distinct(self) -> None:
        self.assertEqual(len({KATO_STARTED_IT, CHAT_OPENED_FOR_IT, AGENT_IS_WORKING}), 3)


if __name__ == '__main__':
    unittest.main()
