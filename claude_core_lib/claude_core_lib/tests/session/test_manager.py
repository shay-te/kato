from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from claude_core_lib.claude_core_lib.session.manager import (
    SESSION_STATUS_ACTIVE,
    SESSION_STATUS_DONE,
    ClaudeSessionManager,
    PlanningSessionRecord,
)


class _FakeStreamingSession:
    """Stand-in for StreamingClaudeSession used by the manager tests."""

    def __init__(self, **kwargs) -> None:
        self.task_id = kwargs['task_id']
        self.resume_session_id = kwargs.get('resume_session_id', '')
        self._cwd = kwargs.get('cwd', '/tmp/repo') or '/tmp/repo'
        self._claude_session_id = (
            self.resume_session_id or 'fake-session-' + self.task_id
        )
        self._alive = True
        self.start_calls: list[str] = []
        self.terminate_calls = 0

    @property
    def cwd(self) -> str:
        return self._cwd

    @property
    def claude_session_id(self) -> str:
        return self._claude_session_id

    @property
    def is_alive(self) -> bool:
        return self._alive

    def start(self, initial_prompt: str = '') -> None:
        self.start_calls.append(initial_prompt)

    def terminate(self) -> None:
        self.terminate_calls += 1
        self._alive = False


class ClaudeSessionManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tempdir.cleanup)
        self.state_dir = Path(self._tempdir.name)
        self._fakes: list[_FakeStreamingSession] = []

        def factory(**kwargs):
            session = _FakeStreamingSession(**kwargs)
            self._fakes.append(session)
            return session

        self.manager = ClaudeSessionManager(
            state_dir=self.state_dir,
            session_factory=factory,
        )

    def test_start_session_creates_record_and_persists_to_disk(self) -> None:
        session = self.manager.start_session(
            task_id='PROJ-1',
            task_summary='profile page user section',
            initial_prompt='plan the change',
        )

        # session was started exactly once
        self.assertEqual(session.start_calls, ['plan the change'])

        # in-memory record visible
        record = self.manager.get_record('PROJ-1')
        self.assertIsNotNone(record)
        self.assertEqual(record.task_summary, 'profile page user section')
        self.assertEqual(record.status, SESSION_STATUS_ACTIVE)
        self.assertEqual(record.claude_session_id, session.claude_session_id)

        # persisted as JSON next to the manager
        persisted = json.loads((self.state_dir / 'PROJ-1.json').read_text())
        self.assertEqual(persisted['task_id'], 'PROJ-1')
        self.assertEqual(persisted['claude_session_id'], session.claude_session_id)
        self.assertEqual(persisted['status'], SESSION_STATUS_ACTIVE)

    def test_start_session_returns_existing_live_session(self) -> None:
        first = self.manager.start_session(task_id='PROJ-1')
        second = self.manager.start_session(task_id='PROJ-1')

        self.assertIs(first, second)
        self.assertEqual(len(self._fakes), 1)

    def test_restart_resumes_the_persisted_claude_session_id(self) -> None:
        self.manager.start_session(task_id='PROJ-1')
        # Mark dead and replace by restart-equivalent: drop in-memory state
        # and rebuild a fresh manager pointed at the same state dir.
        self.manager.terminate_session('PROJ-1')

        new_fakes: list[_FakeStreamingSession] = []

        def factory(**kwargs):
            session = _FakeStreamingSession(**kwargs)
            new_fakes.append(session)
            return session

        rebooted = ClaudeSessionManager(
            state_dir=self.state_dir,
            session_factory=factory,
        )
        # The persisted record is still there, but with terminated status.
        record = rebooted.get_record('PROJ-1')
        self.assertIsNotNone(record)

        rebooted.start_session(task_id='PROJ-1')
        self.assertEqual(len(new_fakes), 1)
        self.assertEqual(
            new_fakes[0].resume_session_id,
            record.claude_session_id,
        )

    def test_update_status_persists(self) -> None:
        self.manager.start_session(task_id='PROJ-1')
        self.manager.update_status('PROJ-1', SESSION_STATUS_DONE)

        record = self.manager.get_record('PROJ-1')
        self.assertEqual(record.status, SESSION_STATUS_DONE)
        persisted = json.loads((self.state_dir / 'PROJ-1.json').read_text())
        self.assertEqual(persisted['status'], SESSION_STATUS_DONE)

    def test_update_status_rejects_unknown(self) -> None:
        self.manager.start_session(task_id='PROJ-1')
        with self.assertRaisesRegex(ValueError, 'unknown session status'):
            self.manager.update_status('PROJ-1', 'whatever')

    def test_terminate_session_kills_subprocess_and_keeps_record_by_default(self) -> None:
        session = self.manager.start_session(task_id='PROJ-1')
        self.manager.terminate_session('PROJ-1')

        self.assertEqual(session.terminate_calls, 1)
        self.assertIsNone(self.manager.get_session('PROJ-1'))
        self.assertIsNotNone(self.manager.get_record('PROJ-1'))
        self.assertEqual(
            self.manager.get_record('PROJ-1').status,
            'terminated',
        )

    def test_terminate_session_with_remove_record_clears_disk(self) -> None:
        self.manager.start_session(task_id='PROJ-1')
        self.manager.terminate_session('PROJ-1', remove_record=True)

        self.assertIsNone(self.manager.get_record('PROJ-1'))
        self.assertFalse((self.state_dir / 'PROJ-1.json').exists())

    def test_list_records_returns_all_known_tasks(self) -> None:
        self.manager.start_session(task_id='PROJ-1', task_summary='a')
        self.manager.start_session(task_id='PROJ-2', task_summary='b')

        records = self.manager.list_records()
        ids = sorted(record.task_id for record in records)
        self.assertEqual(ids, ['PROJ-1', 'PROJ-2'])

    def test_load_persisted_records_skips_unreadable_files(self) -> None:
        (self.state_dir / 'corrupt.json').write_text('{not json')
        manager = ClaudeSessionManager(
            state_dir=self.state_dir,
            session_factory=lambda **kwargs: _FakeStreamingSession(**kwargs),
        )
        # Should not raise; corrupt file is silently ignored.
        self.assertEqual(manager.list_records(), [])

    def test_shutdown_terminates_every_live_session(self) -> None:
        self.manager.start_session(task_id='PROJ-1')
        self.manager.start_session(task_id='PROJ-2')
        self.manager.shutdown()
        self.assertTrue(all(fake.terminate_calls == 1 for fake in self._fakes))

    def test_start_session_forwards_docker_mode_on_to_factory(self) -> None:
        captured: dict = {}

        def factory(**kwargs):
            captured.update(kwargs)
            return _FakeStreamingSession(**kwargs)

        manager = ClaudeSessionManager(
            state_dir=self.state_dir,
            session_factory=factory,
        )
        manager.start_session(task_id='PROJ-9', docker_mode_on=True)

        self.assertIs(captured['docker_mode_on'], True)

    def test_start_session_default_docker_mode_is_off(self) -> None:
        captured: dict = {}

        def factory(**kwargs):
            captured.update(kwargs)
            return _FakeStreamingSession(**kwargs)

        manager = ClaudeSessionManager(
            state_dir=self.state_dir,
            session_factory=factory,
        )
        manager.start_session(task_id='PROJ-10')

        self.assertIs(captured['docker_mode_on'], False)

    def test_resume_copies_jsonl_into_target_cwd_project_dir(self) -> None:
        # One-session-per-task invariant: when kato spawns at a cwd
        # different from where the session's JSONL currently lives,
        # the manager copies the JSONL into the new cwd's project dir
        # so ``claude --resume`` finds it. Without this the resume
        # fails silently and a new session id is created — that's the
        # "kato keeps switching sessions" bug.
        import os
        sessions_root = self.state_dir / 'claude-sessions'
        old_cwd_project_dir = sessions_root / '-tmp-old-repo'
        old_cwd_project_dir.mkdir(parents=True)
        session_id = 'old-session-uuid'
        old_jsonl = old_cwd_project_dir / f'{session_id}.jsonl'
        old_jsonl.write_text('{"type": "user"}\n', encoding='utf-8')
        # Persist a record pointing at the old session id.
        record = PlanningSessionRecord(
            task_id='PROJ-77',
            claude_session_id=session_id,
            status='terminated',
            cwd='/tmp/old/repo',
        )
        self.manager._records['PROJ-77'] = record
        self.manager._persist_record(record)

        os.environ['KATO_CLAUDE_SESSIONS_ROOT'] = str(sessions_root)
        self.addCleanup(
            os.environ.pop, 'KATO_CLAUDE_SESSIONS_ROOT', None,
        )
        try:
            self.manager.start_session(
                task_id='PROJ-77',
                cwd='/tmp/new/repo',
            )
        finally:
            pass

        new_cwd_project_dir = sessions_root / '-tmp-new-repo'
        self.assertTrue(
            (new_cwd_project_dir / f'{session_id}.jsonl').is_file(),
            'JSONL should have been copied into the new cwd project dir',
        )


class PlanningSessionRecordTests(unittest.TestCase):
    def test_round_trips_through_dict(self) -> None:
        original = PlanningSessionRecord(
            task_id='PROJ-1',
            task_summary='do the thing',
            claude_session_id='abc',
            status='review',
            created_at_epoch=100.0,
            updated_at_epoch=200.0,
            cwd='/tmp/x',
        )
        round_tripped = PlanningSessionRecord.from_dict(original.to_dict())
        self.assertEqual(round_tripped, original)


if __name__ == '__main__':
    unittest.main()
