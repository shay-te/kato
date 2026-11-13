"""The shared per-turn chat manager, bound to a stand-in backend."""

from __future__ import annotations

import tempfile
import unittest
from unittest.mock import MagicMock, patch

from agent_core_lib.agent_core_lib.session import per_turn_manager as module
from agent_core_lib.agent_core_lib.session.per_turn_manager import PerTurnSessionManager
from agent_core_lib.agent_core_lib.session.record import (
    SESSION_STATUS_ACTIVE,
    SESSION_STATUS_TERMINATED,
    AgentSessionRecord,
)


class _FakeSession:
    DEFAULT_BINARY = 'fakecli'

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.is_alive = True
        self.sent = []
        self.started_with = None
        self.terminated = False
        self._session_id_correction_callback = None

    def start(self, initial_prompt=''):
        self.started_with = initial_prompt

    def send_user_message(self, text):
        self.sent.append(text)

    def terminate(self):
        self.terminated = True


class _Manager(PerTurnSessionManager):
    AGENT_BACKEND = 'fake'
    SESSION_CLASS = _FakeSession
    DISPLAY_NAME = 'Fake'


class _Harness(unittest.TestCase):
    def _manager(self, **kwargs) -> _Manager:
        return _Manager(state_dir=tempfile.mkdtemp(), **kwargs)


class BindingTests(unittest.TestCase):
    def test_an_unbound_manager_refuses_to_build(self) -> None:
        with self.assertRaises(NotImplementedError):
            PerTurnSessionManager(state_dir=tempfile.mkdtemp())


class SessionTests(_Harness):
    def test_a_spawn_builds_a_chat_writes_a_record_and_starts_it(self) -> None:
        sink = MagicMock()
        manager = self._manager(record_sink=sink, git_access_source=lambda: 'strict')
        session = manager.start_session(task_id=' T-1 ', cwd='/w', initial_prompt='hi', task_summary='Fix')
        self.assertEqual(session.kwargs['binary'], 'fakecli')
        self.assertIn('git_access_source', session.kwargs)
        self.assertEqual(session.started_with, 'hi')
        record = sink.call_args.args[0]
        self.assertEqual((record.task_id, record.agent_backend, record.status), ('T-1', 'fake', SESSION_STATUS_ACTIVE))
        self.assertIs(manager.get_session('T-1'), session)

    def test_a_live_chat_is_reused_and_handed_the_opener(self) -> None:
        manager = self._manager()
        first = manager.start_session(task_id='T-1')
        again = manager.start_session(task_id='T-1', initial_prompt='more')
        self.assertIs(first, again)
        self.assertEqual(first.sent, ['more'])
        self.assertIs(manager.start_session(task_id='T-1'), first)

    def test_a_dead_chat_is_forgotten(self) -> None:
        manager = self._manager()
        session = manager.start_session(task_id='T-1')
        session.is_alive = False
        self.assertIsNone(manager.get_session('T-1'))
        self.assertIsNone(manager.get_session('T-2'))

    def test_a_dead_chat_already_replaced_is_left_alone(self) -> None:
        # The liveness read happens outside the lock; a spawn can replace the
        # chat in between, and the newer chat must survive the cleanup.
        manager = self._manager()
        replacement = _FakeSession()

        class _DiesAsItIsReplaced(_FakeSession):
            @property
            def is_alive(self):
                manager._sessions['t-1'] = replacement
                return False

            @is_alive.setter
            def is_alive(self, value):
                pass

        manager._sessions['t-1'] = _DiesAsItIsReplaced()
        self.assertIsNone(manager.get_session('T-1'))
        self.assertIs(manager._sessions['t-1'], replacement)

    def test_terminate_removes_the_chat_and_optionally_its_record(self) -> None:
        manager = self._manager()
        session = manager.start_session(task_id='T-1')
        with patch.object(module, 'delete_record') as delete:
            manager.terminate_session('T-1', remove_record=True)
            manager.terminate_session('T-unknown')
        self.assertTrue(session.terminated)
        delete.assert_called_once()

    def test_shutdown_ends_every_chat_even_if_one_fails(self) -> None:
        logger = MagicMock()
        manager = self._manager(logger=logger)
        first = manager.start_session(task_id='T-1')
        second = manager.start_session(task_id='T-2')
        first.terminate = MagicMock(side_effect=RuntimeError('stuck'))
        manager.shutdown()
        self.assertTrue(second.terminated)
        logger.exception.assert_called_once()


class RecordTests(_Harness):
    def test_the_host_s_record_is_resumed_and_updated_in_place(self) -> None:
        previous = AgentSessionRecord(task_id='T-1', agent_backend='fake', agent_session_id='old-id',
                                      previous_session_ids=['older'])
        manager = self._manager(record_source=lambda task_id: previous)
        session = manager.start_session(task_id='T-1', cwd='/w', task_summary='Summary')
        self.assertEqual(session.kwargs['agent_session_id'], 'old-id')
        self.assertEqual(previous.previous_session_ids, ['older'])
        self.assertEqual(previous.task_summary, 'Summary')
        manager.terminate_session('T-1')
        manager.start_session(task_id='T-1', task_summary='A newer summary')
        self.assertEqual(previous.task_summary, 'Summary')  # an existing summary is kept

    def test_another_backend_s_record_is_never_resumed(self) -> None:
        theirs = AgentSessionRecord(task_id='T-1', agent_backend='claude', agent_session_id='claude-id')
        manager = self._manager(record_source=lambda task_id: theirs)
        session = manager.start_session(task_id='T-1')
        self.assertEqual(session.kwargs['agent_session_id'], '')

    def test_an_explicit_id_wins_and_a_failing_source_is_logged(self) -> None:
        logger = MagicMock()
        manager = self._manager(record_source=MagicMock(side_effect=OSError('x')), logger=logger)
        session = manager.start_session(task_id='T-1', agent_session_id=' pinned ')
        self.assertEqual(session.kwargs['agent_session_id'], 'pinned')
        logger.exception.assert_called()

    def test_the_learned_id_is_recorded_unless_the_record_moved_on(self) -> None:
        sink = MagicMock()
        manager = self._manager(record_sink=sink)
        session = manager.start_session(task_id='T-1')
        session._session_id_correction_callback('learned')
        self.assertEqual(sink.call_args.args[0].agent_session_id, 'learned')
        moved = AgentSessionRecord(task_id='T-1', agent_backend='claude')
        manager._record_session_id(moved, 'late')
        self.assertEqual(moved.agent_session_id, '')

    def test_persist_failures_are_logged_never_raised(self) -> None:
        logger = MagicMock()
        manager = self._manager(record_sink=MagicMock(side_effect=RuntimeError('ui gone')), logger=logger)
        with patch.object(module, 'write_record', side_effect=OSError('disk full')):
            manager._persist(AgentSessionRecord(task_id='T-1'))
        self.assertEqual(logger.exception.call_count, 2)

    def test_mark_terminated(self) -> None:
        record = AgentSessionRecord(task_id='T-1', agent_backend='fake')
        self._manager().mark_terminated(record)
        self.assertEqual(record.status, SESSION_STATUS_TERMINATED)


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
