"""Coverage for the review-comment switch on ``/api/all-settings``.

Turning ``KATO_REVIEW_COMMENTS_ENABLED`` off from the Settings drawer has
to do BOTH halves of what the operator asked for: stop the next poll (the
persisted value does that — the gate reads settings.json fresh) and stop
the review-comment run already in flight, which needs the live service.
This pins the route half: the stop call, the "no restart needed" verdict,
and the message the drawer shows.

Also pins the default-ON fill so the toggle never draws unchecked while
the code behind it is enabled.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from kato_webserver.app import create_app

KEY = 'KATO_REVIEW_COMMENTS_ENABLED'


class _FakeManager:
    def list_records(self):
        return []
    def get_record(self, task_id):  # noqa: ARG002
        return None
    def get_session(self, task_id):  # noqa: ARG002
        return None


class ReviewCommentSwitchRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.settings_path = Path(self._tmp.name) / 'settings.json'
        patcher = patch.dict(
            os.environ, {'KATO_SETTINGS_FILE': str(self.settings_path)},
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        # ``_persist_settings`` mirrors saves into os.environ; keep that out
        # of the real process env once the test ends.
        self.addCleanup(os.environ.pop, KEY, None)
        os.environ.pop(KEY, None)
        self.agent_service = Mock()
        self.agent_service.stop_review_comment_work = Mock(return_value=[])

    def _app(self):
        return create_app(
            session_manager=_FakeManager(), agent_service=self.agent_service,
        )

    def _post(self, value):
        return self._app().test_client().post(
            '/api/all-settings', json={'updates': {KEY: value}},
        )

    def _saved(self):
        if not self.settings_path.is_file():
            return {}
        return json.loads(self.settings_path.read_text(encoding='utf-8'))

    def test_turning_off_persists_and_stops_in_flight_runs(self) -> None:
        self.agent_service.stop_review_comment_work.return_value = ['PROJ-1']

        resp = self._post(False)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._saved()[KEY], 'false')
        self.agent_service.stop_review_comment_work.assert_called_once_with()
        body = resp.get_json()
        # The switch applies live — telling the operator to restart would be
        # a lie, and would leave them unsure whether it took effect.
        self.assertFalse(body['restart_required'])
        self.assertIn('PROJ-1', body['message'])

    def test_turning_off_with_nothing_running_still_reports_live(self) -> None:
        resp = self._post(False)

        body = resp.get_json()
        self.assertFalse(body['restart_required'])
        self.assertIn('stopped pulling', body['message'])
        self.assertNotIn('run(s) in progress', body['message'])

    def test_turning_on_does_not_stop_anything(self) -> None:
        resp = self._post(True)

        self.assertEqual(self._saved()[KEY], 'true')
        self.agent_service.stop_review_comment_work.assert_not_called()
        self.assertFalse(resp.get_json()['restart_required'])

    def test_a_failing_teardown_does_not_fail_the_save(self) -> None:
        self.agent_service.stop_review_comment_work.side_effect = RuntimeError('boom')

        resp = self._post(False)

        # The write already succeeded and the gate already blocks the next
        # poll — reporting a 500 here would push the operator to re-save.
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._saved()[KEY], 'false')

    def test_mixed_save_still_reports_restart_required(self) -> None:
        # The General tab POSTs every dirty field at once. A restart-only
        # key riding along with the switch must keep the banner up — and
        # the switch must still take effect immediately.
        resp = self._app().test_client().post(
            '/api/all-settings',
            json={'updates': {KEY: False, 'KATO_LOG_LEVEL': 'debug'}},
        )

        body = resp.get_json()
        self.agent_service.stop_review_comment_work.assert_called_once_with()
        self.assertEqual(self._saved()[KEY], 'false')
        self.assertTrue(body['restart_required'])
        self.assertIn('stopped pulling', body['message'])
        self.assertIn('Restart kato', body['message'])

    def test_other_keys_still_report_restart_required(self) -> None:
        resp = self._app().test_client().post(
            '/api/all-settings', json={'updates': {'KATO_LOG_LEVEL': 'debug'}},
        )
        self.assertTrue(resp.get_json()['restart_required'])
        self.agent_service.stop_review_comment_work.assert_not_called()

    def test_unset_renders_as_on(self) -> None:
        resp = self._app().test_client().get('/api/all-settings')
        general = next(
            s for s in resp.get_json()['sections'] if s['id'] == 'general'
        )
        field = next(f for f in general['fields'] if f['key'] == KEY)
        self.assertEqual(field['type'], 'bool')
        self.assertEqual(field['value'], 'true')
        self.assertEqual(field['source'], 'default')

    def test_saved_value_is_read_back_not_the_boot_env_snapshot(self) -> None:
        # Boot copies settings.json into os.environ and the resolver reads
        # env first, so without the mirror in ``_persist_settings`` the GET
        # would keep serving the BOOT value and the toggle would spring
        # back to "on" the moment the drawer refreshed.
        os.environ[KEY] = 'true'
        self._post(False)

        resp = self._app().test_client().get('/api/all-settings')
        general = next(
            s for s in resp.get_json()['sections'] if s['id'] == 'general'
        )
        field = next(f for f in general['fields'] if f['key'] == KEY)
        self.assertEqual(field['value'], 'false')


if __name__ == '__main__':
    unittest.main()
