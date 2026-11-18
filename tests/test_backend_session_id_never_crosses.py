"""A parked agent must never write its session id onto another agent's record.

The bug, as it reached the operator: a task's record held the SAME id under
``agent_session_id`` (claude) and ``chats_by_backend['codex']``.

How it got there. Switching the chat to another agent parks the outgoing one —
``switch_backend`` moves the record to the new backend and clears its session
id — but the outgoing SUBPROCESS is deliberately left running so switching
back resumes it. Its reader thread then fires a late init, and the id-recording
path had no backend guard, so it wrote the parked agent's id onto a record now
owned by the other one. The next switch-back parked that foreign id under the
other backend's key.

Why it matters more than a wrong label: resuming that chat hands one CLI the
other's transcript id. It finds no transcript, starts blank, and the operator's
conversation looks lost — the same shape as the resume-amnesia bugs this
codebase has fixed before.
"""
from __future__ import annotations

import logging
import unittest
from types import SimpleNamespace

from agent_core_lib.agent_core_lib.session.backend_chats import (
    parked_chat,
    switch_backend,
)
from agent_core_lib.agent_core_lib.session.record import AgentSessionRecord
from claude_core_lib.claude_core_lib.session.manager import ClaudeSessionManager
from codex_core_lib.codex_core_lib.session.manager import CodexSessionManager


def _record(backend='claude', session_id='claude-id-1'):
    return AgentSessionRecord(
        task_id='T1',
        task_summary='',
        agent_backend=backend,
        agent_session_id=session_id,
        previous_session_ids=[],
        chats_by_backend={},
    )


class ClaudeIdNeverLandsOnAnotherBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manager = ClaudeSessionManager.__new__(ClaudeSessionManager)
        self.manager.logger = logging.getLogger('test')
        self.manager._lock = __import__('threading').RLock()
        self.manager._records = {}
        self.manager._sessions = {}
        self.manager._persist_record = lambda record: None

    def _correct(self, record, actual_id, source=None):
        self.manager._records['T1'] = record
        self.manager._correct_session_id_in_record(
            'T1', 'T1', actual_id, source_session=source,
        )

    def test_a_parked_claude_does_not_write_onto_a_codex_record(self) -> None:
        # THE BUG. The record has moved to codex and been cleared; the still
        # -running Claude subprocess reports its id a moment later.
        record = _record(backend='codex', session_id='')
        self._correct(record, 'claude-id-1')
        self.assertEqual(record.agent_session_id, '')

    def test_it_still_records_while_the_record_is_on_claude(self) -> None:
        record = _record(backend='claude', session_id='')
        session = SimpleNamespace()
        self.manager._sessions['T1'] = session
        self._correct(record, 'claude-id-1', source=session)
        self.assertEqual(record.agent_session_id, 'claude-id-1')

    def test_a_legacy_record_with_no_backend_is_still_written(self) -> None:
        # Written before backends were tracked — it belongs to whoever runs.
        record = _record(backend='', session_id='')
        session = SimpleNamespace()
        self.manager._sessions['T1'] = session
        self._correct(record, 'claude-id-1', source=session)
        self.assertEqual(record.agent_session_id, 'claude-id-1')


class CodexIdNeverLandsOnAnotherBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manager = CodexSessionManager.__new__(CodexSessionManager)
        self.manager.logger = logging.getLogger('test')
        self.manager._persist = lambda record: None

    def test_a_parked_codex_does_not_write_onto_a_claude_record(self) -> None:
        record = _record(backend='claude', session_id='claude-id-1')
        self.manager._record_session_id(record, 'codex-thread-1')
        self.assertEqual(record.agent_session_id, 'claude-id-1')

    def test_it_still_records_while_the_record_is_on_codex(self) -> None:
        record = _record(backend='codex', session_id='')
        self.manager._record_session_id(record, 'codex-thread-1')
        self.assertEqual(record.agent_session_id, 'codex-thread-1')

    def test_a_legacy_record_with_no_backend_is_still_written(self) -> None:
        record = _record(backend='', session_id='')
        self.manager._record_session_id(record, 'codex-thread-1')
        self.assertEqual(record.agent_session_id, 'codex-thread-1')


class ReadingTheRecordDoesNotCrossIdsTests(unittest.TestCase):
    """The path the operator actually hit — and the one first missed.

    ``_with_refreshed_session_id`` runs on EVERY ``get_record`` and
    ``list_records``. Its two preconditions — the record's id is empty, and a
    live session is still reporting one — are exactly what switching tabs
    leaves behind, because parking clears the id and deliberately keeps the
    subprocess alive.

    That makes it the easiest of the three writers to reach: the UI's
    permission poller calls ``list_records`` every couple of seconds, so the
    corruption came back within seconds of a tab switch even with the other
    two writers guarded. Guarding only the id-REPORTING paths was not enough;
    merely READING the record re-created the bug.
    """

    def _manager(self, record, *, live_id):
        manager = ClaudeSessionManager.__new__(ClaudeSessionManager)
        manager.logger = logging.getLogger('test')
        manager._lock = __import__('threading').RLock()
        manager._records = {'T1': record}
        manager._sessions = {
            'T1': SimpleNamespace(is_alive=True, agent_session_id=live_id),
        }
        manager._persist_record = lambda saved: None
        manager._lookup_key = lambda task_id: task_id
        manager._discard_if_session_id_drifted_locked = (
            lambda *args, **kwargs: False
        )
        return manager

    def test_reading_a_parked_record_does_not_adopt_the_live_id(self) -> None:
        record = _record(backend='codex', session_id='')
        manager = self._manager(record, live_id='claude-id-1')
        manager._with_refreshed_session_id(record)
        self.assertEqual(record.agent_session_id, '')

    def test_reading_its_own_record_still_learns_the_live_id(self) -> None:
        # The guard must not break the feature: a Claude record with no id yet
        # SHOULD pick up the id its live session reports.
        record = _record(backend='claude', session_id='')
        manager = self._manager(record, live_id='claude-id-1')
        manager._with_refreshed_session_id(record)
        self.assertEqual(record.agent_session_id, 'claude-id-1')

    def test_a_legacy_record_with_no_backend_still_learns_it(self) -> None:
        record = _record(backend='', session_id='')
        manager = self._manager(record, live_id='claude-id-1')
        manager._with_refreshed_session_id(record)
        self.assertEqual(record.agent_session_id, 'claude-id-1')


class TheCorruptionCannotBeReproducedTests(unittest.TestCase):
    """The whole sequence, end to end, as the operator hit it."""

    def test_switching_tabs_never_gives_two_backends_the_same_id(self) -> None:
        claude = ClaudeSessionManager.__new__(ClaudeSessionManager)
        claude.logger = logging.getLogger('test')
        claude._lock = __import__('threading').RLock()
        claude._sessions = {}
        claude._persist_record = lambda record: None

        # 1. A task working on Claude.
        record = _record(backend='claude', session_id='claude-id-1')

        # 2. The operator opens the Codex tab. Claude is PARKED, not killed.
        switch_backend(record, 'codex')
        self.assertEqual(record.agent_backend, 'codex')
        self.assertEqual(record.agent_session_id, '')

        # 3. The parked Claude subprocess reports its id late.
        claude._records = {'T1': record}
        claude._correct_session_id_in_record('T1', 'T1', 'claude-id-1')

        # 3b. And the UI merely READS the record — which the permission
        # poller does every couple of seconds. This is the step the first
        # version of the fix missed, and on its own it was enough to
        # re-create the corruption.
        claude._sessions['T1'] = SimpleNamespace(
            is_alive=True, agent_session_id='claude-id-1',
        )
        claude._lookup_key = lambda task_id: task_id
        claude._discard_if_session_id_drifted_locked = (
            lambda *args, **kwargs: False
        )
        claude._with_refreshed_session_id(record)

        # 4. The operator switches back.
        switch_backend(record, 'claude')

        # Codex must own NOTHING — it never ran.
        self.assertEqual(parked_chat(record, 'codex')['agent_session_id'], '')
        # And Claude still owns its own conversation.
        self.assertEqual(record.agent_session_id, 'claude-id-1')



class StartupRecoveryNeverFilesClaudeUnderAnotherAgentTests(unittest.TestCase):
    """UNA-3475 (2026-11-18): at startup the Claude manager recovers Claude ids
    from each task's workspace metadata. It checked only the WORKSPACE's agent
    stamp, so a task whose record was on Codex — its Codex chat with no id yet —
    got the Claude id written onto the Codex chat; the next switch back filed it
    under Codex and the Codex tab opened blank."""

    def setUp(self) -> None:
        self.manager = ClaudeSessionManager.__new__(ClaudeSessionManager)
        self.manager.logger = logging.getLogger('test')
        self.manager._lock = __import__('threading').RLock()
        self.manager._records = {}
        self.manager._sessions = {}
        self.persisted = []
        self.manager._persist_record = self.persisted.append
        self.workspace = SimpleNamespace(
            task_id='T1', task_summary='', cwd='/w/T1', agent_backend='claude', agent_session_id='claude-id-1',
        )
        self.manager._workspace_manager = SimpleNamespace(list_workspaces=lambda: [self.workspace])

    def _seed(self):
        self.manager._seed_records_from_workspaces()

    def test_a_record_on_codex_with_no_codex_id_yet_is_left_alone(self) -> None:
        record = _record(backend='codex', session_id='')
        record.chats_by_backend = {'claude': {'agent_session_id': 'claude-id-1', 'previous_session_ids': []}}
        self.manager._records['t1'] = record
        self._seed()
        self.assertEqual(record.agent_session_id, '')
        self.assertEqual(self.persisted, [])
        # …so switching back to Claude finds its own chat, and Codex keeps none of Claude's.
        switch_backend(record, 'claude')
        self.assertEqual(record.agent_session_id, 'claude-id-1')
        self.assertEqual(parked_chat(record, 'codex')['agent_session_id'], '')

    def test_a_task_with_no_record_still_gets_its_claude_id_back(self) -> None:
        self._seed()
        self.assertEqual(self.manager._records['t1'].agent_session_id, 'claude-id-1')
        self.assertEqual(len(self.persisted), 1)

    def test_a_claude_record_with_no_id_still_gets_it_back(self) -> None:
        record = _record(backend='claude', session_id='')
        self.manager._records['t1'] = record
        self._seed()
        self.assertEqual(record.agent_session_id, 'claude-id-1')


class ParkedClaudeIsJudgedByItsOwnChatTests(unittest.TestCase):
    """The drift check compared a live Claude id with the record's id while the
    record was on Codex — the Codex chat's — and killed the parked Claude chat
    so its next spawn would resume the Codex id (kato.log, 22:58:51)."""

    def setUp(self) -> None:
        self.manager = ClaudeSessionManager.__new__(ClaudeSessionManager)
        self.manager.logger = logging.getLogger('test')
        self.manager._records = {}
        self.manager._sessions = {}
        self.terminated = []

    def _session(self, live_id):
        return SimpleNamespace(agent_session_id=live_id, resume_was_ignored=False,
                               terminate=lambda: self.terminated.append(live_id))

    def test_a_parked_claude_chat_matching_its_own_id_is_kept(self) -> None:
        record = _record(backend='codex', session_id='codex-thread-1')
        record.chats_by_backend = {'claude': {'agent_session_id': 'claude-id-1', 'previous_session_ids': []}}
        self.manager._records['t1'] = record
        session = self._session('claude-id-1')
        self.manager._sessions['t1'] = session
        self.assertFalse(self.manager._discard_if_session_id_drifted_locked('t1', 'T1', session))
        self.assertEqual(self.terminated, [])

    def test_a_claude_chat_that_really_drifted_from_its_own_id_is_still_stopped(self) -> None:
        record = _record(backend='claude', session_id='claude-id-1')
        self.manager._records['t1'] = record
        session = self._session('claude-id-OTHER')
        self.manager._sessions['t1'] = session
        self.assertTrue(self.manager._discard_if_session_id_drifted_locked('t1', 'T1', session))
        self.assertEqual(self.terminated, ['claude-id-OTHER'])

if __name__ == '__main__':
    unittest.main()
