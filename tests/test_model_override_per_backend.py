"""Model and effort are per (task, BACKEND) — not per task.

Reported as an unexplained ``turn.aborted`` on the first Codex message. The
model picker is per-task, so a task switched to the Codex tab inherited the
Claude alias the operator had chosen, kato ran ``codex exec -m opus``, and
the CLI answered:

    The 'opus' model is not supported when using Codex with a ChatGPT account

then exited. A model name is only meaningful to the backend that publishes
it, so the override store is keyed on both.
"""

from __future__ import annotations

import tempfile
import unittest
from unittest.mock import MagicMock

from kato_webserver.app import create_app, _build_fallback_manager


class OverrideScopingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix='kato-override-')
        self.addCleanup(self._tmp.cleanup)
        self.manager = MagicMock()
        self.backend = 'claude'
        self.manager.backend_for.side_effect = lambda task_id: self.backend
        self.app = create_app(
            session_manager=self.manager,
            agent_service=MagicMock(),
            fallback_state_dir=self._tmp.name,
        )
        self.client = self.app.test_client()

    def _set_model(self, model):
        return self.client.post('/api/sessions/T1/model', json={'model': model})

    def _get_model(self):
        return self.client.get('/api/sessions/T1/model').get_json()['model']

    def _set_effort(self, effort):
        return self.client.post('/api/sessions/T1/effort', json={'effort': effort})

    def _get_effort(self):
        return self.client.get('/api/sessions/T1/effort').get_json()['effort']

    def test_a_model_picked_for_claude_is_not_served_to_codex(self) -> None:
        """The bug, exactly."""
        self._set_model('opus')
        self.assertEqual(self._get_model(), 'opus')

        self.backend = 'codex'
        self.assertEqual(
            self._get_model(), '',
            "Codex inherited Claude's model — `codex exec -m opus` is refused "
            'by the CLI and kills the turn',
        )

    def test_each_backend_keeps_its_own_model(self) -> None:
        self._set_model('opus')
        self.backend = 'codex'
        self._set_model('gpt-5-codex')
        self.assertEqual(self._get_model(), 'gpt-5-codex')

        self.backend = 'claude'
        self.assertEqual(self._get_model(), 'opus')

    def test_clearing_one_backend_leaves_the_other(self) -> None:
        self._set_model('opus')
        self.backend = 'codex'
        self._set_model('gpt-5-codex')
        self._set_model('')
        self.assertEqual(self._get_model(), '')

        self.backend = 'claude'
        self.assertEqual(self._get_model(), 'opus')

    def test_effort_is_scoped_the_same_way(self) -> None:
        self._set_effort('high')
        self.assertEqual(self._get_effort(), 'high')

        self.backend = 'codex'
        self.assertEqual(self._get_effort(), '')

    def test_a_host_with_no_backend_resolver_still_works(self) -> None:
        # Single-backend host: the key degrades to the bare task id rather
        # than losing the override entirely.
        with tempfile.TemporaryDirectory(prefix='kato-override-2-') as tmp:
            app = create_app(
                session_manager=_build_fallback_manager(tmp),
                agent_service=MagicMock(),
                fallback_state_dir=tmp,
            )
            client = app.test_client()
            client.post('/api/sessions/T1/model', json={'model': 'opus'})
            self.assertEqual(
                client.get('/api/sessions/T1/model').get_json()['model'], 'opus',
            )

    def test_a_resolver_that_raises_does_not_break_the_route(self) -> None:
        self.manager.backend_for.side_effect = RuntimeError('down')
        response = self._set_model('opus')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._get_model(), 'opus')


if __name__ == '__main__':
    unittest.main()
