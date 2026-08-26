"""A chat message runs on the tab it was typed into.

Reported on Windows: the CLAUDE tab answered with "failed to launch codex:
[WinError 2]". The backend was re-derived from the session RECORD at send
time, so a UI whose session poll had not caught up could send from one tab
while the record still named the other — and kato launched that CLI.

The tab is the operator's actual intent, so the message carries it and the
record is re-pointed before anything spawns.
"""

from __future__ import annotations

import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from kato_webserver.app import create_app


class SendAlignsTheBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix='kato-send-backend-')
        self.addCleanup(self._tmp.cleanup)
        self.record = SimpleNamespace(
            task_id='T1', agent_backend='codex', agent_session_id='c1',
            previous_session_ids=[], chats_by_backend={},
        )
        self.manager = MagicMock()
        self.manager.backend_for.side_effect = (
            lambda task_id: self.record.agent_backend
        )
        self.manager.get_record.return_value = self.record
        self.manager.available_backends.return_value = ['claude', 'codex']
        self.manager.get_session.return_value = None
        self.app = create_app(
            session_manager=self.manager,
            agent_service=MagicMock(),
            fallback_state_dir=self._tmp.name,
        )
        self.client = self.app.test_client()

    def _send(self, body):
        return self.client.post('/api/sessions/T1/messages', json=body)

    def test_a_claude_tab_message_repoints_a_codex_record(self) -> None:
        """The reported bug, exactly."""
        self._send({'text': 'hello', 'agent_backend': 'claude'})
        self.assertEqual(self.record.agent_backend, 'claude')
        self.manager.save_record.assert_called()

    def test_a_matching_backend_is_left_alone(self) -> None:
        self._send({'text': 'hello', 'agent_backend': 'codex'})
        self.assertEqual(self.record.agent_backend, 'codex')
        self.manager.save_record.assert_not_called()

    def test_an_older_ui_that_names_nothing_changes_nothing(self) -> None:
        self._send({'text': 'hello'})
        self.assertEqual(self.record.agent_backend, 'codex')
        self.manager.save_record.assert_not_called()

    def test_an_unwired_backend_is_refused_not_switched_to(self) -> None:
        # Re-pointing at a backend kato cannot run would swap one broken
        # spawn for another.
        self.manager.available_backends.return_value = ['claude']
        self._send({'text': 'hello', 'agent_backend': 'codex'})
        self.assertEqual(self.record.agent_backend, 'codex')

    def test_an_unknown_backend_name_is_ignored(self) -> None:
        self._send({'text': 'hello', 'agent_backend': 'not-a-backend'})
        self.assertEqual(self.record.agent_backend, 'codex')

    def test_a_failure_to_align_does_not_fail_the_message(self) -> None:
        # A chat message must never 500 because the record could not be
        # re-pointed.
        self.manager.get_record.side_effect = RuntimeError('store down')
        response = self._send({'text': 'hello', 'agent_backend': 'claude'})
        self.assertNotEqual(response.status_code, 500)

    def test_no_record_yet_is_a_no_op(self) -> None:
        self.manager.get_record.return_value = None
        response = self._send({'text': 'hello', 'agent_backend': 'claude'})
        self.assertNotEqual(response.status_code, 500)


class RoutingIsLoggedTests(unittest.TestCase):
    """Every chat message says which agent it ran on.

    Routing is decided across the browser, the session record and the
    router. When an operator reports "it went to the wrong agent" there was
    nothing to read — only guesses about which of the three disagreed.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix='kato-routing-log-')
        self.addCleanup(self._tmp.cleanup)
        self.record = SimpleNamespace(
            task_id='T1', agent_backend='codex', agent_session_id='',
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

    def _logged(self, body):
        with patch.object(self.app, 'logger') as logger:
            self.app.test_client().post('/api/sessions/T1/messages', json=body)
            return ' '.join(
                str(c.args[0]) % c.args[1:] if len(c.args) > 1 else str(c.args[0])
                for c in logger.info.call_args_list
            )

    def test_it_names_the_tab_and_the_backend(self) -> None:
        line = self._logged({'text': 'hi', 'agent_backend': 'codex'})
        self.assertIn('from the codex tab', line)
        self.assertIn('running on codex', line)

    def test_a_mismatch_is_visible_in_the_log(self) -> None:
        # The tab wins (the record is re-pointed), and the log shows both.
        line = self._logged({'text': 'hi', 'agent_backend': 'claude'})
        self.assertIn('from the claude tab', line)
        self.assertIn('running on claude', line)

    def test_a_caller_that_names_nothing_is_still_logged(self) -> None:
        line = self._logged({'text': 'hi'})
        self.assertIn('(none) tab', line)

    def test_logging_never_fails_the_send(self) -> None:
        self.manager.backend_for.side_effect = RuntimeError('down')
        response = self.app.test_client().post(
            '/api/sessions/T1/messages', json={'text': 'hi'},
        )
        self.assertNotEqual(response.status_code, 500)


class SleepingSessionSpawnsTheSendersTabTests(unittest.TestCase):
    """The Code review button's real path: a task with no live subprocess.

    Reported as "the code review prompt it's going to claude instead of
    codex". Code review routes through the composer send path deliberately —
    so it wakes a sleeping session — and THAT branch spawns a fresh
    subprocess rather than delivering into a running one. A normal message on
    a live session never reaches it, so it needs its own coverage: the spawn
    has to use the CLI of the tab the operator pressed the button on.
    """

    def setUp(self) -> None:
        from kato_core_lib.data_layers.service.planning_session_runner import (
            PlanningSessionRunner,
            StreamingSessionDefaults,
        )
        self._tmp = tempfile.TemporaryDirectory(prefix='kato-sleep-spawn-')
        self.addCleanup(self._tmp.cleanup)
        self.record = SimpleNamespace(
            task_id='T1', task_summary='s', agent_backend='claude',
            agent_session_id='', previous_session_ids=[], chats_by_backend={},
            cwd='/w', expected_branch='',
        )
        self.manager = MagicMock()
        self.manager.backend_for.side_effect = (
            lambda t: self.record.agent_backend
        )
        self.manager.get_record.return_value = self.record
        self.manager.available_backends.return_value = ['claude', 'codex']
        self.manager.get_session.return_value = None   # sleeping
        self.runner = PlanningSessionRunner(
            session_manager=self.manager,
            defaults=StreamingSessionDefaults(binary='claude'),
            defaults_by_backend={
                'claude': StreamingSessionDefaults(binary='claude'),
                'codex': StreamingSessionDefaults(binary='codex'),
            },
        )
        self.app = create_app(
            session_manager=self.manager, agent_service=MagicMock(),
            planning_session_runner=self.runner,
            fallback_state_dir=self._tmp.name,
        )
        self.app.config['WORKSPACE_MANAGER'] = None

    def _send(self, backend):
        with patch('kato_webserver.app._chat_resume_context',
                   return_value=('/w', 's', '')), \
             patch('kato_webserver.app._chat_additional_dirs', return_value=[]):
            self.app.test_client().post(
                '/api/sessions/T1/messages',
                json={'text': 'review my changes', 'agent_backend': backend},
            )
        call = self.manager.start_session.call_args
        return call.kwargs.get('binary') if call else None

    def test_a_codex_tab_review_spawns_codex(self) -> None:
        """The reported bug, on the path code review actually takes."""
        self.assertEqual(self._send('codex'), 'codex')
        self.assertEqual(self.record.agent_backend, 'codex')

    def test_a_claude_tab_review_spawns_claude(self) -> None:
        self.record.agent_backend = 'codex'
        self.assertEqual(self._send('claude'), 'claude')

    def test_an_untagged_send_uses_the_record(self) -> None:
        self.record.agent_backend = 'codex'
        with patch('kato_webserver.app._chat_resume_context',
                   return_value=('/w', 's', '')), \
             patch('kato_webserver.app._chat_additional_dirs', return_value=[]):
            self.app.test_client().post(
                '/api/sessions/T1/messages', json={'text': 'hi'},
            )
        self.assertEqual(
            self.manager.start_session.call_args.kwargs.get('binary'), 'codex',
        )


if __name__ == '__main__':
    unittest.main()
