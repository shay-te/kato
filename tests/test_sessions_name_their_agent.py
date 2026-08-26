"""Every task in the session list says WHICH agent it runs on.

The status chip read ``session.agent_backend`` and, finding it empty, showed
the literal word "Agent" — which answers nothing, since the whole question is
which agent. The rows are built from WORKSPACE records, and those carry no
backend: they describe the clone on disk, not the chat.

The session layer knows (``backend_for`` = the record's backend, else the
configured default), so the list asks it.
"""

from __future__ import annotations

import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from kato_webserver.app import create_app


def _workspace(task_id):
    return SimpleNamespace(
        task_id=task_id,
        to_dict=lambda: {'task_id': task_id, 'status': 'active'},
    )


class SessionListNamesTheAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix='kato-session-backend-')
        self.addCleanup(self._tmp.cleanup)
        self.backends = {'T1': 'codex', 'T2': 'claude'}
        self.manager = MagicMock()
        self.manager.backend_for.side_effect = lambda t: self.backends.get(t, '')
        self.manager.list_records.return_value = []
        self.workspace_manager = MagicMock()
        self.workspace_manager.list_workspaces.return_value = [
            _workspace('T1'), _workspace('T2'),
        ]

    def _rows(self):
        app = create_app(
            session_manager=self.manager,
            workspace_manager=self.workspace_manager,
            agent_service=MagicMock(),
            fallback_state_dir=self._tmp.name,
        )
        body = app.test_client().get('/api/sessions').get_json()
        return {row['task_id']: row for row in body}

    def test_each_task_reports_its_own_backend(self) -> None:
        rows = self._rows()
        self.assertEqual(rows['T1']['agent_backend'], 'codex')
        self.assertEqual(rows['T2']['agent_backend'], 'claude')

    def test_the_field_is_always_present(self) -> None:
        """Absent, the UI has nothing to name the agent with."""
        for row in self._rows().values():
            self.assertIn('agent_backend', row)

    def test_a_task_with_no_chat_yet_still_names_one(self) -> None:
        # backend_for falls back to the configured default, so a task whose
        # chat has never started still says which CLI would run.
        self.backends = {}
        self.manager.backend_for.side_effect = lambda t: 'claude'
        self.assertEqual(self._rows()['T1']['agent_backend'], 'claude')

    def test_a_resolver_that_raises_does_not_break_the_list(self) -> None:
        self.manager.backend_for.side_effect = RuntimeError('down')
        rows = self._rows()
        self.assertEqual(rows['T1']['agent_backend'], '')
        self.assertEqual(rows['T1']['task_id'], 'T1')

    def test_a_manager_without_a_resolver_uses_its_own_name(self) -> None:
        # Single-backend host: one manager, and it names itself.
        manager = MagicMock(spec=['AGENT_BACKEND', 'list_records'])
        manager.AGENT_BACKEND = 'claude'
        manager.list_records.return_value = []
        app = create_app(
            session_manager=manager,
            workspace_manager=self.workspace_manager,
            agent_service=MagicMock(),
            fallback_state_dir=self._tmp.name,
        )
        body = app.test_client().get('/api/sessions').get_json()
        self.assertEqual(body[0]['agent_backend'], 'claude')


if __name__ == '__main__':
    unittest.main()
