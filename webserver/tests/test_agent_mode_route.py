"""``/api/sessions/<task_id>/agent-mode`` — the composer's Modes picker.

The stored value is the literal ``--permission-mode`` the next spawn uses, so
an unvalidated value would not fail here — it would fail at spawn, minutes
later, looking like "kato stopped responding" rather than "that mode is not a
thing". Hence the allow-list at the route.

The mode is persisted for the same reason plan mode always was: it is a
SAFETY posture. A task left on Manual must come back on Manual after a
restart, not silently drop to the permissive default.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kato_webserver.app import create_app


class _Manager:
    def list_records(self):
        return []

    def get_record(self, task_id):  # noqa: ARG002
        return None

    def get_session(self, task_id):  # noqa: ARG002
        return None


class AgentModeRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = Path(self._tmp.name) / 'plan_mode.json'
        patcher = patch.dict(os.environ, {'KATO_PLAN_MODE_PATH': str(self.store)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.app = create_app(session_manager=_Manager())
        self.client = self.app.test_client()

    def _set(self, mode):
        return self.client.post(
            '/api/sessions/T1/agent-mode', json={'mode': mode},
        )

    def test_defaults_to_the_configured_default(self) -> None:
        body = self.client.get('/api/sessions/T1/agent-mode').get_json()
        self.assertEqual(body['mode'], '')

    def test_round_trips_every_supported_mode(self) -> None:
        for mode in ('default', 'plan', 'bypassPermissions', 'acceptEdits', ''):
            with self.subTest(mode=mode):
                self.assertEqual(self._set(mode).status_code, 200)
                self.assertEqual(
                    self.client.get('/api/sessions/T1/agent-mode').get_json()['mode'],
                    mode,
                )

    def test_unknown_mode_is_refused_at_the_route(self) -> None:
        response = self._set('yolo')
        self.assertEqual(response.status_code, 400)
        self.assertIn('allowed', response.get_json())
        # …and nothing was stored, so the next spawn is unaffected.
        self.assertEqual(
            self.client.get('/api/sessions/T1/agent-mode').get_json()['mode'], '',
        )

    def test_the_mode_is_persisted_for_the_next_boot(self) -> None:
        self._set('plan')
        self.assertEqual(json.loads(self.store.read_text()), {'T1': 'plan'})

    def test_a_persisted_mode_is_reloaded_at_boot(self) -> None:
        self.store.write_text(json.dumps({'T1': 'default', 'T2': 'plan'}))
        app = create_app(session_manager=_Manager())
        client = app.test_client()
        self.assertEqual(
            client.get('/api/sessions/T1/agent-mode').get_json()['mode'], 'default',
        )
        self.assertEqual(
            client.get('/api/sessions/T2/agent-mode').get_json()['mode'], 'plan',
        )

    def test_a_legacy_plan_lock_survives_the_upgrade(self) -> None:
        # The pre-modes file was a list of plan-locked task ids. Reading it as
        # "no modes set" would quietly release a safety lock on upgrade.
        self.store.write_text(json.dumps(['T9']))
        app = create_app(session_manager=_Manager())
        client = app.test_client()
        self.assertEqual(
            client.get('/api/sessions/T9/agent-mode').get_json()['mode'], 'plan',
        )
        self.assertTrue(client.get('/api/sessions/T9/plan-mode').get_json()['plan_mode'])

    def test_plan_chosen_from_the_modes_menu_shows_as_plan_mode(self) -> None:
        # Both surfaces describe the same override; they must not disagree.
        self._set('plan')
        self.assertTrue(self.client.get('/api/sessions/T1/plan-mode').get_json()['plan_mode'])
        self._set('')
        self.assertFalse(self.client.get('/api/sessions/T1/plan-mode').get_json()['plan_mode'])


class SessionContextUsageTests(unittest.TestCase):
    """``/api/sessions/<id>`` carries context usage for the composer meter."""

    def test_missing_session_reports_unknown_not_zero_percent(self) -> None:
        class _Recorded(_Manager):
            def get_record(self, task_id):  # noqa: ARG002
                return {'task_id': 'T1'}

        app = create_app(session_manager=_Recorded())
        body = app.test_client().get('/api/sessions/T1').get_json()
        self.assertEqual(
            body['context_usage'],
            {'used_tokens': 0, 'limit_tokens': 0, 'model': ''},
        )


if __name__ == '__main__':
    unittest.main()
