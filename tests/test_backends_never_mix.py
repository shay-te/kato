"""Claude and Codex must never bleed into each other.

A task can hold a live chat with each backend at once, and every place the
two meet is a chance to hand one agent the other's state. Each of these has
been a real bug at some point in this feature's life:

  * Claude's JSONL transcript replayed into the Codex tab;
  * a message typed in one tab tagged with the other's backend;
  * the wrong CLI binary spawned for a tab;
  * a session id issued by one backend handed to the other.

These are the invariants, in one place, so a future change to any single
surface cannot quietly re-open one of them.
"""

from __future__ import annotations

import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from agent_core_lib.agent_core_lib.session.backend_chats import (
    parked_chat,
    switch_backend,
)
from agent_core_lib.agent_core_lib.session.record import AgentSessionRecord
from claude_core_lib.claude_core_lib.session.history import (
    resolve_agent_session_id,
)


class ChatsStayWithTheirBackendTests(unittest.TestCase):
    """Switching tabs parks one conversation and lifts the other."""

    def _record(self):
        return AgentSessionRecord(
            task_id='T1', agent_backend='claude', agent_session_id='claude-1',
        )

    def test_switching_parks_the_outgoing_chat(self) -> None:
        record = self._record()
        switch_backend(record, 'codex')
        self.assertEqual(record.agent_backend, 'codex')
        # Claude's id is parked, NOT carried over to Codex.
        self.assertEqual(record.agent_session_id, '')
        self.assertEqual(
            parked_chat(record, 'claude')['agent_session_id'], 'claude-1',
        )

    def test_switching_back_restores_the_original_chat(self) -> None:
        record = self._record()
        switch_backend(record, 'codex')
        record.agent_session_id = 'codex-1'
        switch_backend(record, 'claude')
        self.assertEqual(record.agent_session_id, 'claude-1')
        self.assertEqual(
            parked_chat(record, 'codex')['agent_session_id'], 'codex-1',
        )

    def test_neither_backend_can_see_the_others_id(self) -> None:
        record = self._record()
        switch_backend(record, 'codex')
        record.agent_session_id = 'codex-1'
        self.assertNotEqual(
            parked_chat(record, 'claude')['agent_session_id'],
            parked_chat(record, 'codex')['agent_session_id'],
        )


class TranscriptsStayWithTheirBackendTests(unittest.TestCase):
    """Claude's JSONL must never be replayed into a Codex tab."""

    def _manager(self, backend, session_id):
        manager = Mock()
        manager.get_record.return_value = SimpleNamespace(
            agent_backend=backend, agent_session_id=session_id,
        )
        return manager

    def _workspace(self, session_id):
        manager = Mock()
        manager.get.return_value = SimpleNamespace(agent_session_id=session_id)
        return manager

    def test_a_codex_chat_resolves_no_claude_transcript(self) -> None:
        resolved = resolve_agent_session_id(
            self._manager('codex', 'codex-1'), None, 'T1',
        )
        self.assertEqual(resolved, '')

    def test_the_workspace_mirror_cannot_leak_claudes_id(self) -> None:
        # The mirror is written by the Claude session and never cleared on a
        # switch — the exact path that put Claude's chat in the Codex tab.
        resolved = resolve_agent_session_id(
            self._manager('codex', ''), self._workspace('claude-1'), 'T1',
        )
        self.assertEqual(resolved, '')

    def test_a_claude_chat_still_resolves_normally(self) -> None:
        resolved = resolve_agent_session_id(
            self._manager('claude', 'claude-1'), None, 'T1',
        )
        self.assertEqual(resolved, 'claude-1')


class SpawnsUseTheirOwnCliTests(unittest.TestCase):
    """Each tab spawns ITS binary and model, never the configured default's."""

    def _runner(self, backend):
        from kato_core_lib.data_layers.service.planning_session_runner import (
            PlanningSessionRunner,
            StreamingSessionDefaults,
        )
        manager = MagicMock()
        manager.backend_for.return_value = backend
        runner = PlanningSessionRunner(
            session_manager=manager,
            defaults=StreamingSessionDefaults(binary='claude', model='opus'),
            defaults_by_backend={
                'claude': StreamingSessionDefaults(binary='claude', model='opus'),
                'codex': StreamingSessionDefaults(
                    binary='codex', model='gpt-5-codex',
                ),
            },
        )
        runner._start_session(
            task_id='T1', task_summary='s', initial_prompt='go', cwd='/w',
        )
        return manager.start_session.call_args.kwargs

    def test_a_codex_task_never_spawns_the_claude_binary(self) -> None:
        self.assertEqual(self._runner('codex')['binary'], 'codex')

    def test_a_codex_task_never_gets_a_claude_model(self) -> None:
        # ``opus`` is refused by the Codex API and kills the turn outright.
        self.assertEqual(self._runner('codex')['model'], 'gpt-5-codex')

    def test_a_claude_task_is_unaffected(self) -> None:
        kwargs = self._runner('claude')
        self.assertEqual(kwargs['binary'], 'claude')
        self.assertEqual(kwargs['model'], 'opus')


class PerTaskOverridesStayWithTheirBackendTests(unittest.TestCase):
    """A model picked for one agent is never served to the other."""

    def setUp(self) -> None:
        from kato_webserver.app import create_app
        self._tmp = tempfile.TemporaryDirectory(prefix='kato-nomix-')
        self.addCleanup(self._tmp.cleanup)
        self.backend = 'claude'
        manager = MagicMock()
        manager.backend_for.side_effect = lambda t: self.backend
        self.client = create_app(
            session_manager=manager, agent_service=MagicMock(),
            fallback_state_dir=self._tmp.name,
        ).test_client()

    def test_a_claude_model_is_invisible_to_codex(self) -> None:
        self.client.post('/api/sessions/T1/model', json={'model': 'opus'})
        self.backend = 'codex'
        self.assertEqual(
            self.client.get('/api/sessions/T1/model').get_json()['model'], '',
        )

    def test_effort_is_scoped_the_same_way(self) -> None:
        self.client.post('/api/sessions/T1/effort', json={'effort': 'high'})
        self.backend = 'codex'
        self.assertEqual(
            self.client.get('/api/sessions/T1/effort').get_json()['effort'], '',
        )


class MessagesRunOnTheSendersTabTests(unittest.TestCase):
    """A message never runs on a backend the operator was not looking at."""

    def setUp(self) -> None:
        from kato_webserver.app import create_app
        self._tmp = tempfile.TemporaryDirectory(prefix='kato-nomix-msg-')
        self.addCleanup(self._tmp.cleanup)
        self.record = SimpleNamespace(
            task_id='T1', agent_backend='claude', agent_session_id='',
            previous_session_ids=[], chats_by_backend={},
        )
        self.manager = MagicMock()
        self.manager.backend_for.side_effect = (
            lambda t: self.record.agent_backend
        )
        self.manager.get_record.return_value = self.record
        self.manager.available_backends.return_value = ['claude', 'codex']
        self.manager.get_session.return_value = None
        self.app = create_app(
            session_manager=self.manager, agent_service=MagicMock(),
            fallback_state_dir=self._tmp.name,
        )

    def test_the_senders_tab_wins_over_a_stale_record(self) -> None:
        with patch('kato_webserver.app._chat_resume_context',
                   return_value=('/w', 's', '')), \
             patch('kato_webserver.app._chat_additional_dirs', return_value=[]):
            self.app.test_client().post(
                '/api/sessions/T1/messages',
                json={'text': 'hi', 'agent_backend': 'codex'},
            )
        self.assertEqual(self.record.agent_backend, 'codex')


if __name__ == '__main__':
    unittest.main()


class HistoryReplayUsesTheRightReaderTests(unittest.TestCase):
    """Each backend's transcript is read by its own reader, never the other's.

    Codex had no reader at all — its events lived in memory, so a chat
    survived a page reload but not a restart. Adding one re-opens the mixing
    question: the readers must stay bound to their own backend, or a Codex
    tab shows Claude's conversation again.
    """

    def _replay(self, backend, session_id):
        from kato_webserver import app as app_module
        record = SimpleNamespace(
            agent_backend=backend, agent_session_id=session_id,
        )
        claude_calls, codex_calls = [], []
        with patch.object(
            app_module, '_replay_history_from_disk',
            lambda sid: claude_calls.append(sid) or iter(()),
        ), patch.object(
            app_module, '_replay_codex_history_from_disk',
            lambda rec: codex_calls.append(rec.agent_session_id) or iter(()),
        ):
            list(app_module._replay_history(record, session_id))
        return claude_calls, codex_calls

    def test_a_codex_chat_uses_the_codex_reader(self) -> None:
        claude, codex = self._replay('codex', 'codex-1')
        self.assertEqual(codex, ['codex-1'])
        self.assertEqual(claude, [])

    def test_a_claude_chat_uses_the_claude_reader(self) -> None:
        claude, codex = self._replay('claude', 'claude-1')
        self.assertEqual(claude, ['claude-1'])
        self.assertEqual(codex, [])

    def test_a_legacy_record_with_no_backend_reads_as_claude(self) -> None:
        # Records predating backend tracking are all Claude; reading them as
        # anything else would blank every pre-existing chat.
        claude, codex = self._replay('', 'claude-1')
        self.assertEqual(claude, ['claude-1'])
        self.assertEqual(codex, [])

    def test_the_codex_reader_uses_the_RECORD_id(self) -> None:
        # NOT the shared resolver: that one deliberately answers '' for a
        # Codex chat so it can never hand back Claude's transcript id.
        _claude, codex = self._replay('codex', 'codex-1')
        self.assertEqual(codex, ['codex-1'])
