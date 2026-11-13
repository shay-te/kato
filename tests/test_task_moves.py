"""Every ticket move goes through ONE service, ``TaskMoves`` — and only it.

Real ``LocalAwareTaskStateService`` (the class kato wires), real
``LocalTaskStore`` on disk, real ``TaskDataAccess``, and the real callers
(``AgentService``, ``WaitPlanningService``, ``TaskFailureHandler``,
``TrackerStepLauncher``); only the tracker's HTTP client is an in-memory board
that keeps each ticket's state, and a live chat is a plain stand-in with the
attributes a session has. No mocks.

``SingleMoverGateTests`` reads the source: a move made anywhere but
``task_moves.py`` fails the build, so a new feature cannot grow its own copy.
"""

from __future__ import annotations

import ast
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
from kato_core_lib.data_layers.service.plan_queue.tracker_launcher import TrackerStepLauncher
from kato_core_lib.data_layers.service.restart_resume.running_agents import (
    is_mid_turn,
    running_task_ids,
)
from kato_core_lib.data_layers.service.task_failure_handler import TaskFailureHandler
from kato_core_lib.data_layers.service.task_moves import (
    AGENT_IS_WORKING,
    CHAT_OPENED_FOR_IT,
    COUNTED_DONE_ON_ITS_PLAN,
    DONE,
    HANDED_OVER,
    IN_PROGRESS,
    IN_REVIEW,
    ITS_PULL_REQUESTS_ARE_OPEN,
    ITS_RUN_FAILED,
    KATO_STARTED_IT,
    OPEN,
    OPERATOR_MARKED_IT_DONE,
    OPERATOR_PRESSED_DONE,
    TaskMoves,
)
from kato_core_lib.data_layers.service.wait_planning_service import WaitPlanningService
from local_task_core_lib.local_task_core_lib.data.local_task import LocalTaskState
from local_task_core_lib.local_task_core_lib.store import LocalTaskStore
from tests.utils import build_task

TRACKER_OPEN, TRACKER_PROGRESS, TRACKER_REVIEW, TRACKER_DONE = (
    'Open', 'In Progress', 'Pull Request', 'Done',
)
REPO_ROOT = Path(__file__).resolve().parent.parent


class _Board(object):
    """A tracker in memory: each ticket has a state, and a move changes it."""

    def __init__(self, tickets: dict[str, str], *, refuses: str = '') -> None:
        self.states = dict(tickets)
        self.refuses = refuses

    def move_issue_to_state(self, issue_id, field, state):
        if self.refuses:
            raise RuntimeError(self.refuses)
        self.states[issue_id] = state


class _DoneHeard(object):
    def __init__(self) -> None:
        self.tasks: list[str] = []

    def task_done(self, task_id) -> None:
        self.tasks.append(task_id)


class _Comments(object):
    def __init__(self) -> None:
        self.added: list[tuple[str, str]] = []

    def add_comment(self, task_id, body) -> None:
        self.added.append((task_id, body))


class _Chat(object):
    def __init__(self, *, alive=True, working=False, asking='') -> None:
        self.is_alive = alive
        self.is_working = working
        self._asking = asking

    def pending_control_request_tool(self):
        return self._asking


class _Chats(object):
    """What the session manager answers: the task's live chat, if any."""

    def __init__(self, chats: dict[str, _Chat] | None = None) -> None:
        self._chats = dict(chats or {})

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
        project='PROJ', assignee='me', issue_states=[TRACKER_OPEN],
        open_state=TRACKER_OPEN, progress_state=TRACKER_PROGRESS,
        review_state=TRACKER_REVIEW, done_state=TRACKER_DONE,
        progress_state_field='State', review_state_field='State',
        done_state_field='State', open_state_field='State',
    )


class _Setup(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = LocalTaskStore(Path(tmp.name) / 'local_tasks.json')
        self.board = _Board({'UNA-1': TRACKER_OPEN, 'UNA-2': TRACKER_OPEN})
        self.done_heard = _DoneHeard()
        self.states = self._states(self.board)
        self.logger = logging.getLogger(f'test_task_moves.{self.id()}')
        self.moves = TaskMoves(self.states, logger=self.logger)

    def _states(self, client):
        return LocalAwareTaskStateService(
            _config(), TaskDataAccess(_config(), client), self.store,
            done_observer=self.done_heard,
        )

    def _refusing(self, why: str) -> TaskMoves:
        return TaskMoves(self._states(_Board({'UNA-1': TRACKER_OPEN}, refuses=why)), logger=self.logger)

    def local_task(self, state=LocalTaskState.OPEN) -> str:
        return self.store.create('local', 'details', state=state).id


class MoveTests(_Setup):
    def test_each_move_puts_a_tracker_ticket_in_its_configured_state(self) -> None:
        for state, tracker_state in (
            (IN_PROGRESS, TRACKER_PROGRESS), (IN_REVIEW, TRACKER_REVIEW),
            (DONE, TRACKER_DONE), (OPEN, TRACKER_OPEN),
        ):
            with self.subTest(state=state):
                self.moves.move('UNA-1', state, 'a test')
                self.assertEqual(self.board.states['UNA-1'], tracker_state)
        self.assertEqual(self.board.states['UNA-2'], TRACKER_OPEN)

    def test_each_move_puts_a_local_task_in_its_state_never_on_the_tracker(self) -> None:
        task_id = self.local_task()
        for state, local_state in (
            (IN_PROGRESS, LocalTaskState.IN_PROGRESS), (IN_REVIEW, LocalTaskState.REVIEW),
            (DONE, LocalTaskState.DONE), (OPEN, LocalTaskState.OPEN),
        ):
            with self.subTest(state=state):
                self.moves.move(task_id, state, 'a test')
                self.assertEqual(self.store.get(task_id).state, local_state)
        self.assertEqual(self.board.states, {'UNA-1': TRACKER_OPEN, 'UNA-2': TRACKER_OPEN})

    def test_the_log_says_where_and_why(self) -> None:
        with self.assertLogs(self.logger, level='INFO') as logs:
            self.moves.move('UNA-1', IN_PROGRESS, KATO_STARTED_IT)
        self.assertIn('task UNA-1 moved to in progress: kato started working on it', logs.output[0])

    def test_done_is_heard_by_the_done_observers_and_only_done(self) -> None:
        self.moves.move('UNA-1', IN_REVIEW, OPERATOR_PRESSED_DONE)
        self.assertEqual(self.done_heard.tasks, [])
        self.moves.move('UNA-1', DONE, OPERATOR_MARKED_IT_DONE)
        self.assertEqual(self.done_heard.tasks, ['UNA-1'])

    def test_a_refused_done_is_heard_by_no_one(self) -> None:
        with self.assertRaisesRegex(RuntimeError, 'closed project'):
            self._refusing('closed project').move('UNA-1', DONE, OPERATOR_MARKED_IT_DONE)
        self.assertEqual(self.done_heard.tasks, [])

    def test_a_refusing_tracker_is_raised_to_the_caller(self) -> None:
        with self.assertRaisesRegex(RuntimeError, 'no such state'):
            self._refusing('no such state').move('UNA-1', IN_PROGRESS, KATO_STARTED_IT)

    def test_a_state_kato_does_not_know_is_refused_before_the_tracker(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot be moved to 'archived'"):
            self.moves.move('UNA-1', 'archived', 'a test')
        self.assertEqual(self.board.states['UNA-1'], TRACKER_OPEN)

    def test_no_tracker_refuses_a_ticket_but_moves_a_local_task(self) -> None:
        moves = TaskMoves(self._states(NoTrackerClient()), logger=self.logger)
        task_id = self.local_task()
        moves.move(task_id, IN_PROGRESS, CHAT_OPENED_FOR_IT)
        self.assertEqual(self.store.get(task_id).state, LocalTaskState.IN_PROGRESS)
        with self.assertRaisesRegex(ValueError, 'this install has no issue tracker'):
            moves.move('UNA-1', IN_PROGRESS, CHAT_OPENED_FOR_IT)

    def test_a_default_logger_is_made_when_none_is_given(self) -> None:
        self.assertIsNotNone(TaskMoves(self.states).logger)


class TryMoveTests(_Setup):
    def test_true_once_moved(self) -> None:
        self.assertTrue(self.moves.try_move('UNA-1', IN_PROGRESS, CHAT_OPENED_FOR_IT))
        self.assertEqual(self.board.states['UNA-1'], TRACKER_PROGRESS)

    def test_false_and_logged_when_the_tracker_refuses_never_raised(self) -> None:
        moves = self._refusing('the workflow forbids it')
        with self.assertLogs(self.logger, level='ERROR') as logs:
            self.assertFalse(moves.try_move('UNA-1', IN_PROGRESS, CHAT_OPENED_FOR_IT))
        self.assertIn('could not move task UNA-1 to in progress (kato opened its chat)', logs.output[0])

    def test_an_unknown_state_is_false_too(self) -> None:
        with self.assertLogs(self.logger, level='ERROR'):
            self.assertFalse(self.moves.try_move('UNA-1', 'archived', 'a test'))


class MoveIfAgentWorkingTests(_Setup):
    def test_a_working_agent_moves_its_ticket(self) -> None:
        chats = _Chats({'UNA-1': _Chat(working=True)})
        self.assertTrue(self.moves.move_if_agent_working('UNA-1', chats))
        self.assertEqual(self.board.states, {'UNA-1': TRACKER_PROGRESS, 'UNA-2': TRACKER_OPEN})

    def test_an_agent_waiting_for_the_operator_inside_its_turn_counts_as_working(self) -> None:
        chats = _Chats({'UNA-1': _Chat(working=False, asking='Bash')})
        self.assertTrue(self.moves.move_if_agent_working('UNA-1', chats))
        self.assertEqual(self.board.states['UNA-1'], TRACKER_PROGRESS)

    def test_an_idle_chat_moves_nothing(self) -> None:
        # A run that failed moved the ticket back to Open on purpose, and its
        # chat stays alive afterwards: only WORK moves it forward again.
        chats = _Chats({'UNA-1': _Chat(working=False)})
        self.assertFalse(self.moves.move_if_agent_working('UNA-1', chats))
        self.assertEqual(self.board.states['UNA-1'], TRACKER_OPEN)

    def test_a_dead_chat_moves_nothing(self) -> None:
        chats = _Chats({'UNA-1': _Chat(alive=False, working=True)})
        self.assertFalse(self.moves.move_if_agent_working('UNA-1', chats))

    def test_no_chat_no_manager_or_an_unreadable_one_moves_nothing(self) -> None:
        for chats in (_Chats(), None, _Unreadable()):
            with self.subTest(chats=chats):
                self.assertFalse(self.moves.move_if_agent_working('UNA-1', chats))
        self.assertEqual(self.board.states['UNA-1'], TRACKER_OPEN)

    def test_working_but_the_tracker_refuses_is_still_working(self) -> None:
        moves = self._refusing('offline')
        with self.assertLogs(self.logger, level='ERROR') as logs:
            self.assertTrue(moves.move_if_agent_working('UNA-1', _Chats({'UNA-1': _Chat(working=True)})))
        self.assertIn(AGENT_IS_WORKING, logs.output[0])

    def test_a_local_task_s_working_agent_moves_it_in_the_store(self) -> None:
        task_id = self.local_task()
        self.assertTrue(self.moves.move_if_agent_working(task_id, _Chats({task_id: _Chat(working=True)})))
        self.assertEqual(self.store.get(task_id).state, LocalTaskState.IN_PROGRESS)


class FailedRunThenContinueTests(_Setup):
    """The report: autopilot tasks kato was working on, still in Open."""

    def test_open_after_the_failed_run_in_progress_once_the_agent_works_again(self) -> None:
        handler = TaskFailureHandler(
            _MustNotRun('task_service'), self.states, _MustNotRun('repository_service'),
            _MustNotRun('notification_service'), logger=self.logger,
        )
        self.moves.move('UNA-1', IN_PROGRESS, KATO_STARTED_IT)
        self.assertTrue(handler._move_task_to_open('UNA-1'))  # the failed run's way back
        self.assertEqual(self.board.states['UNA-1'], TRACKER_OPEN)
        self.assertFalse(self.moves.move_if_agent_working('UNA-1', _Chats({'UNA-1': _Chat()})))
        self.assertEqual(self.board.states['UNA-1'], TRACKER_OPEN)
        # The operator types "continue": the agent works again.
        self.assertTrue(self.moves.move_if_agent_working('UNA-1', _Chats({'UNA-1': _Chat(working=True)})))
        self.assertEqual(self.board.states['UNA-1'], TRACKER_PROGRESS)


class CallersMoveThroughTheServiceTests(_Setup):
    """Each real caller, with the real state service: the move and its why."""

    def _agent(self, chats=None) -> AgentService:
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

    def _says(self, logs, why: str) -> None:
        self.assertTrue(any(why in line for line in logs.output), logs.output)

    def test_the_queue_scan_moves_a_ticket_whose_agent_works_and_starts_nothing(self) -> None:
        agent = self._agent(_Chats({'UNA-1': _Chat(working=True)}))
        with self.assertLogs(self.logger, level='INFO') as logs:
            result = agent.process_assigned_task(Task(id='UNA-1', summary='x'))
        self.assertEqual(self.board.states['UNA-1'], TRACKER_PROGRESS)
        self.assertIsNotNone(result)  # a skip: no implementation, no testing, no publish
        self._says(logs, AGENT_IS_WORKING)

    def test_kato_s_own_start(self) -> None:
        agent = self._agent()
        agent._task_publisher = types.SimpleNamespace(comment_task_started=lambda *a: None)
        with self.assertLogs(self.logger, level='INFO') as logs:
            self.assertTrue(agent._start_task_processing(
                Task(id='UNA-2'), types.SimpleNamespace(repositories=[]),
            ))
        self.assertEqual(self.board.states['UNA-2'], TRACKER_PROGRESS)
        self._says(logs, KATO_STARTED_IT)

    def test_the_operator_marking_it_done(self) -> None:
        agent = self._agent()
        with self.assertLogs(self.logger, level='INFO') as logs:
            agent.mark_task_done(' UNA-1 ')
        self.assertEqual(self.board.states['UNA-1'], TRACKER_DONE)
        self.assertEqual(self.done_heard.tasks, ['UNA-1'])
        self._says(logs, OPERATOR_MARKED_IT_DONE)
        with self.assertRaisesRegex(ValueError, 'task id is required'):
            agent.mark_task_done('  ')

    def test_a_planning_chat_whose_chat_already_runs(self) -> None:
        planning = WaitPlanningService(
            session_manager=_Chats({'UNA-1': _Chat()}),
            repository_service=_MustNotRun('repository_service'),
            task_state_service=self.states,
        )
        planning.logger = self.logger
        with self.assertLogs(self.logger, level='INFO') as logs:
            self.assertIsNotNone(
                planning.handle_task(build_task(task_id='UNA-1', tags=['kato:wait-planning'])),
            )
        self.assertEqual(self.board.states['UNA-1'], TRACKER_PROGRESS)
        self._says(logs, CHAT_OPENED_FOR_IT)

    def test_a_failed_run_back_to_open_never_raises(self) -> None:
        handler = TaskFailureHandler(
            _MustNotRun('task_service'), self._states(_Board({}, refuses='offline')),
            _MustNotRun('repository_service'), _MustNotRun('notification_service'),
            logger=self.logger,
        )
        with self.assertLogs(self.logger, level='ERROR') as logs:
            self.assertFalse(handler._move_task_to_open('UNA-1'))
        self._says(logs, ITS_RUN_FAILED)

    def test_a_plan_step_counted_as_done(self) -> None:
        comments = _Comments()
        launcher = TrackerStepLauncher(
            task_service=comments, task_state_service=self.states,
            repository_service=_MustNotRun('repository_service'), ownership=None,
        )
        launcher.counted_done('UNA-2')
        self.assertEqual(self.board.states['UNA-2'], TRACKER_DONE)
        self.assertEqual(self.done_heard.tasks, ['UNA-2'])
        self.assertEqual([task for task, _body in comments.added], ['UNA-2'])

    def test_every_why_is_its_own_words(self) -> None:
        whys = [
            KATO_STARTED_IT, CHAT_OPENED_FOR_IT, AGENT_IS_WORKING, ITS_RUN_FAILED,
            ITS_PULL_REQUESTS_ARE_OPEN, OPERATOR_PRESSED_DONE, OPERATOR_MARKED_IT_DONE,
            COUNTED_DONE_ON_ITS_PLAN, HANDED_OVER,
        ]
        self.assertEqual(len(set(whys)), len(whys))


class RunningAgentsTests(unittest.TestCase):
    def test_is_mid_turn_without_a_manager_is_false(self) -> None:
        self.assertFalse(is_mid_turn(None, 'UNA-1'))

    def test_running_task_ids_lists_working_and_asking_chats_only(self) -> None:
        chats = _Chats({
            'UNA-1': _Chat(working=True), 'UNA-2': _Chat(asking='Edit'), 'UNA-3': _Chat(),
            'UNA-4': _Chat(alive=False, working=True),
        })
        self.assertEqual(running_task_ids(chats), ['UNA-1', 'UNA-2'])

    def test_running_task_ids_without_a_manager_or_an_unreadable_one_is_empty(self) -> None:
        self.assertEqual(running_task_ids(None), [])
        self.assertEqual(running_task_ids(_Unreadable()), [])


#: The state service's moves, and the data access / client calls beneath them.
_MOVE_CALLS = frozenset({
    'move_task_to_in_progress', 'move_task_to_review', 'move_task_to_open',
    'move_task_to_done', 'move_task_to_state', 'move_issue_to_state',
})
#: The ONLY files allowed to call them — the mover, and the layers beneath it.
_ALLOWED = {
    'kato_core_lib/data_layers/service/task_moves.py': {
        'move_task_to_in_progress', 'move_task_to_review', 'move_task_to_open', 'move_task_to_done',
    },
    'kato_core_lib/data_layers/service/task_state_service.py': {'move_task_to_state'},
    'kato_core_lib/data_layers/data_access/task_data_access.py': {'move_issue_to_state'},
}
_SCANNED = ('kato_core_lib', 'webserver/kato_webserver')


def _move_calls_outside_the_mover() -> list[str]:
    found = []
    for root in _SCANNED:
        for path in sorted((REPO_ROOT / root).rglob('*.py')):
            relative = path.relative_to(REPO_ROOT).as_posix()
            if '/tests/' in f'/{relative}':
                continue
            tree = ast.parse(path.read_text(encoding='utf-8'), filename=relative)
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                    continue
                name = node.func.attr
                if name not in _MOVE_CALLS or name in _ALLOWED.get(relative, ()):
                    continue
                if _is_super_call(node.func.value):
                    continue  # an override handing the move on (local tasks), not a caller
                found.append(f'{relative}:{node.lineno} {name}')
    return found


def _is_super_call(node) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == 'super')


class SingleMoverGateTests(unittest.TestCase):
    """A ticket is moved by ``TaskMoves`` and nothing else — read from the source."""

    def test_no_code_moves_a_ticket_but_the_mover(self) -> None:
        self.assertEqual(
            _move_calls_outside_the_mover(), [],
            'move tickets through TaskMoves (task_moves.py), never the state service directly',
        )

    def test_the_gate_sees_a_direct_move(self) -> None:
        tree = ast.parse('self._task_state_service.move_task_to_review(task.id)')
        call = tree.body[0].value
        self.assertIn(call.func.attr, _MOVE_CALLS)
        self.assertFalse(_is_super_call(call.func.value))
        self.assertTrue(_is_super_call(ast.parse('super().move_task_to_open(x)').body[0].value.func.value))

    def test_the_gate_scans_real_files(self) -> None:
        for relative in _ALLOWED:
            self.assertTrue((REPO_ROOT / relative).is_file(), relative)


if __name__ == '__main__':
    unittest.main()
