"""Codex spawns must resume the task's chat, and must not erase its record.

Two defects lived here, and both were invisible from the outside because the
route that adopts a session returned 200 either way.

1. The manager could only resume from an explicit parameter. Nothing in the
   host passed one, so an id an operator adopted through the UI — which writes
   the record and nothing else — never reached ``codex exec resume``. Every
   spawn opened a blank thread and the adopted conversation was silently gone.

2. Every spawn built a REPLACEMENT record. Persisting is a whole-file write,
   so anything not re-listed on the new object was erased: the task's parked
   chats under other backends, its ``previous_session_ids``, its context
   counters. The loss surfaced only at the next restart.
"""

from __future__ import annotations

import tempfile
import unittest
from types import SimpleNamespace

from agent_core_lib.agent_core_lib.session.record import AgentSessionRecord
from codex_core_lib.codex_core_lib.session.manager import CodexSessionManager


class _FakeSession(object):
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.is_alive = False
        self._session_id_correction_callback = None

    def start(self, initial_prompt=''):
        self.is_alive = True

    def send_user_message(self, text):
        pass


class CodexSpawnTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.spawned: list[dict] = []
        self.persisted: list[AgentSessionRecord] = []

    def _manager(self, record=None):
        def factory(**kwargs):
            self.spawned.append(kwargs)
            return _FakeSession(**kwargs)

        return CodexSessionManager(
            state_dir=self._tmp.name,
            session_factory=factory,
            record_sink=self.persisted.append,
            record_source=lambda task_id: record,
            logger=SimpleNamespace(
                exception=lambda *a, **k: None,
                info=lambda *a, **k: None,
                warning=lambda *a, **k: None,
                error=lambda *a, **k: None,
                debug=lambda *a, **k: None,
            ),
        )

    def _record(self, **overrides):
        base = dict(
            task_id='T1', task_summary='sum', agent_backend='codex',
            agent_session_id='thread-9', previous_session_ids=['older-1'],
            chats_by_backend={'claude': {'agent_session_id': 'claude-1'}},
        )
        base.update(overrides)
        return AgentSessionRecord(**base)

    # ----- 1. the adopted id must reach the CLI --------------------------

    def test_it_resumes_the_id_on_the_record(self) -> None:
        manager = self._manager(self._record())
        manager.start_session(task_id='T1', cwd='/w')
        self.assertEqual(self.spawned[0]['agent_session_id'], 'thread-9')

    def test_an_explicit_id_wins_over_the_record(self) -> None:
        manager = self._manager(self._record())
        manager.start_session(task_id='T1', cwd='/w', agent_session_id='explicit-2')
        self.assertEqual(self.spawned[0]['agent_session_id'], 'explicit-2')

    def test_no_record_starts_a_fresh_thread(self) -> None:
        manager = self._manager(None)
        manager.start_session(task_id='T1', cwd='/w')
        self.assertEqual(self.spawned[0]['agent_session_id'], '')

    def test_it_never_resumes_another_backend_s_id(self) -> None:
        # An id only resolves in the CLI that issued it. Handing a Claude
        # session id to ``codex exec resume`` finds nothing and opens blank.
        manager = self._manager(
            self._record(agent_backend='claude', agent_session_id='claude-1'),
        )
        manager.start_session(task_id='T1', cwd='/w')
        self.assertEqual(self.spawned[0]['agent_session_id'], '')

    # ----- 2. the record must survive the spawn ---------------------------

    def test_the_spawn_preserves_parked_chats_and_history(self) -> None:
        record = self._record()
        manager = self._manager(record)
        manager.start_session(task_id='T1', cwd='/w')
        saved = self.persisted[-1]
        self.assertEqual(
            saved.chats_by_backend, {'claude': {'agent_session_id': 'claude-1'}},
        )
        self.assertEqual(saved.previous_session_ids, ['older-1'])

    def test_the_spawn_updates_the_same_record_object(self) -> None:
        record = self._record()
        manager = self._manager(record)
        manager.start_session(task_id='T1', cwd='/w', expected_branch='b1')
        self.assertIs(self.persisted[-1], record)
        self.assertEqual(record.expected_branch, 'b1')

    def test_a_record_on_another_backend_is_not_mutated(self) -> None:
        # Switching TO codex is switch_backend's job, not the spawn's; the
        # spawn must not silently repurpose another agent's record.
        record = self._record(agent_backend='claude', agent_session_id='claude-1')
        manager = self._manager(record)
        manager.start_session(task_id='T1', cwd='/w')
        self.assertEqual(record.agent_session_id, 'claude-1')
        self.assertEqual(record.agent_backend, 'claude')

    def test_a_failing_record_source_does_not_break_the_spawn(self) -> None:
        def boom(task_id):
            raise RuntimeError('nope')

        manager = self._manager(None)
        manager._record_source = boom
        manager.start_session(task_id='T1', cwd='/w')
        self.assertEqual(self.spawned[0]['agent_session_id'], '')


if __name__ == '__main__':
    unittest.main()
