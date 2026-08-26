"""Each agent's chat liveness is reported separately.

Both subprocesses can run at once: switching agent tabs parks the outgoing
conversation and DELIBERATELY leaves its process alive. One status chip could
therefore only ever describe the tab in front of the operator, and said
nothing about the agent still working behind it.

``get_session`` answers only for the backend the record names (that is the
chat a message goes to), so reporting both needs a lookup that asks every
manager.
"""

from __future__ import annotations

import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from kato_core_lib.data_layers.service.agent_session_router import (
    AgentSessionRouter,
)
from kato_webserver.app import create_app


def _session(alive=True, working=False):
    return SimpleNamespace(is_alive=alive, is_working=working)


class RouterPerBackendLookupTests(unittest.TestCase):
    def _router(self, claude_session, codex_session):
        claude = MagicMock()
        claude.get_session.return_value = claude_session
        codex = MagicMock()
        codex.get_session.return_value = codex_session
        record_manager = MagicMock()
        record_manager.get_record.return_value = SimpleNamespace(
            agent_backend='claude',
        )
        return AgentSessionRouter(
            managers={'claude': claude, 'codex': codex},
            record_manager=record_manager,
            default_backend='claude',
        )

    def test_both_backends_are_reported(self) -> None:
        router = self._router(_session(), _session(working=True))
        found = router.sessions_by_backend('T1')
        self.assertEqual(sorted(found), ['claude', 'codex'])

    def test_the_parked_backend_is_included(self) -> None:
        """The whole point: the record says claude, codex is still running."""
        router = self._router(_session(), _session(working=True))
        found = router.sessions_by_backend('T1')
        self.assertTrue(found['codex'].is_working)

    def test_a_manager_that_raises_reports_no_session(self) -> None:
        router = self._router(_session(), _session())
        router._managers['codex'].get_session.side_effect = RuntimeError('down')
        found = router.sessions_by_backend('T1')
        self.assertIsNone(found['codex'])
        self.assertIsNotNone(found['claude'])

    def test_a_manager_without_get_session_is_tolerated(self) -> None:
        router = self._router(_session(), _session())
        router._managers['codex'] = MagicMock(spec=[])
        self.assertIsNone(router.sessions_by_backend('T1')['codex'])


class AgentStatusRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix='kato-agent-status-')
        self.addCleanup(self._tmp.cleanup)
        self.manager = MagicMock()
        self.manager.backend_for.return_value = 'claude'
        self.manager.sessions_by_backend.return_value = {
            'claude': _session(alive=True, working=False),
            'codex': _session(alive=True, working=True),
        }
        self.app = create_app(
            session_manager=self.manager,
            agent_service=MagicMock(),
            fallback_state_dir=self._tmp.name,
        )
        self.client = self.app.test_client()

    def _get(self):
        response = self.client.get('/api/sessions/T1/agent-status')
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    def test_it_reports_a_row_per_backend(self) -> None:
        rows = self._get()['backends']
        self.assertEqual([r['id'] for r in rows], ['claude', 'codex'])

    def test_the_active_backend_is_flagged(self) -> None:
        rows = {r['id']: r for r in self._get()['backends']}
        self.assertTrue(rows['claude']['active'])
        self.assertFalse(rows['codex']['active'])

    def test_a_parked_backend_still_reports_working(self) -> None:
        rows = {r['id']: r for r in self._get()['backends']}
        self.assertTrue(rows['codex']['working'])
        self.assertFalse(rows['claude']['working'])

    def test_a_dead_session_reports_not_live(self) -> None:
        self.manager.sessions_by_backend.return_value = {
            'claude': None, 'codex': _session(alive=False),
        }
        rows = {r['id']: r for r in self._get()['backends']}
        self.assertFalse(rows['claude']['live'])
        self.assertFalse(rows['codex']['live'])

    def test_a_failed_lookup_does_not_break_the_route(self) -> None:
        self.manager.sessions_by_backend.side_effect = RuntimeError('down')
        self.assertEqual(self._get()['backends'], [])

    def test_a_manager_without_the_lookup_reports_nothing(self) -> None:
        # An older/simpler manager: the UI falls back to one chip.
        manager = MagicMock(spec=['backend_for'])
        manager.backend_for.return_value = 'claude'
        app = create_app(
            session_manager=manager, agent_service=MagicMock(),
            fallback_state_dir=self._tmp.name,
        )
        body = app.test_client().get('/api/sessions/T1/agent-status').get_json()
        self.assertEqual(body['backends'], [])


if __name__ == '__main__':
    unittest.main()


class PermissionAsksNameTheirAgentTests(unittest.TestCase):
    """An approval prompt must say WHICH agent is asking.

    Both backends can hold a live chat on one task, so an ask with no agent
    on it leaves the operator authorising a command without knowing who will
    run it — and the out-of-sandbox banner asserted it was Claude regardless.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix='kato-perm-agent-')
        self.addCleanup(self._tmp.cleanup)

    def _pending(self, backend):
        session = SimpleNamespace(
            is_alive=True,
            pending_control_requests=lambda: [
                {'request_id': 'r1', 'tool_name': 'Bash', 'input': {}},
            ],
        )
        record = SimpleNamespace(
            task_id='T1', task_summary='support zalo', agent_backend=backend,
        )
        manager = MagicMock()
        manager.list_records.return_value = [record]
        manager.get_session.return_value = session
        app = create_app(
            session_manager=manager,
            agent_service=MagicMock(),
            fallback_state_dir=self._tmp.name,
        )
        body = app.test_client().get('/api/permissions/pending').get_json()
        return body['pending']

    def test_a_codex_ask_is_stamped_codex(self) -> None:
        rows = self._pending('codex')
        self.assertEqual(rows[0]['agent_backend'], 'codex')

    def test_a_claude_ask_is_stamped_claude(self) -> None:
        rows = self._pending('claude')
        self.assertEqual(rows[0]['agent_backend'], 'claude')

    def test_the_field_is_always_present(self) -> None:
        # Absent, the modal has nothing to name the requester with.
        rows = self._pending('')
        self.assertIn('agent_backend', rows[0])
