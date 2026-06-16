"""GET /api/agent-version — reports the configured agent CLI version +
capability flags (powers the out-of-date banner + ultracode gating)."""

from __future__ import annotations

import unittest
from unittest import mock

from kato_webserver.app import create_app


class _Manager:
    def list_records(self):
        return []

    def get_record(self, task_id):  # noqa: ARG002
        return None

    def get_session(self, task_id):  # noqa: ARG002
        return None


_SAMPLE = {
    'backend': 'claude', 'binary': 'claude', 'found': True,
    'version': '2.1.142', 'version_raw': '2.1.142', 'recommended_min': '2.1.160',
    'up_to_date': False, 'supports_workflows': False, 'detail': '',
}


class AgentVersionRouteTests(unittest.TestCase):
    def _app(self):
        return create_app(session_manager=_Manager())

    def test_returns_cached_info_verbatim(self):
        app = self._app()
        app.config['AGENT_VERSION_INFO'] = dict(_SAMPLE)
        body = app.test_client().get('/api/agent-version').get_json()
        self.assertEqual(body['backend'], 'claude')
        self.assertFalse(body['up_to_date'])
        self.assertFalse(body['supports_workflows'])

    def test_computes_and_caches_on_first_call(self):
        app = self._app()
        with mock.patch(
            'kato_core_lib.helpers.agent_version_utils.agent_version_info',
            return_value=dict(_SAMPLE),
        ) as probe:
            first = app.test_client().get('/api/agent-version').get_json()
            second = app.test_client().get('/api/agent-version').get_json()
        self.assertEqual(first['version'], '2.1.142')
        self.assertEqual(second['version'], '2.1.142')
        probe.assert_called_once()  # cached after the first probe

    def test_probe_failure_degrades_gracefully(self):
        app = self._app()
        with mock.patch(
            'kato_core_lib.helpers.agent_version_utils.agent_version_info',
            side_effect=RuntimeError('boom'),
        ):
            body = app.test_client().get('/api/agent-version').get_json()
        # Never 500s the UI; reports a safe "don't nag, don't claim support".
        self.assertTrue(body['up_to_date'])
        self.assertFalse(body['supports_workflows'])


class AgentVersionUpgradeRouteTests(unittest.TestCase):
    def _app(self):
        return create_app(session_manager=_Manager())

    def test_upgrade_runs_and_busts_the_version_cache(self):
        app = self._app()
        app.config['AGENT_VERSION_INFO'] = dict(_SAMPLE)  # pre-cached
        with mock.patch(
            'kato_core_lib.helpers.agent_version_utils.upgrade_agent_cli',
            return_value={'ok': True, 'message': 'upgraded', 'version_after': '2.1.170'},
        ) as up:
            body = app.test_client().post('/api/agent-version/upgrade').get_json()
        self.assertTrue(body['ok'])
        up.assert_called_once()
        self.assertIsNone(app.config.get('AGENT_VERSION_INFO'))  # re-probe next GET

    def test_upgrade_failure_reported_in_body(self):
        app = self._app()
        with mock.patch(
            'kato_core_lib.helpers.agent_version_utils.upgrade_agent_cli',
            return_value={'ok': False, 'message': 'in-app upgrade is disabled'},
        ):
            body = app.test_client().post('/api/agent-version/upgrade').get_json()
        self.assertFalse(body['ok'])
        self.assertIn('disabled', body['message'])


if __name__ == '__main__':
    unittest.main()
