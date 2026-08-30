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
    'up_to_date': False, 'latest_version': '2.1.222', 'update_available': True,
    'supports_workflows': False, 'detail': '',
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


    def test_refresh_also_rechecks_the_published_version(self):
        # Without this the banner can't learn about a release published while
        # this process has been up — the version TTL would have to lapse first.
        #
        # Now a POST: re-probing runs ``<binary> --version`` and calls the npm
        # registry, which is an action rather than a read, and browsers issue
        # GETs nobody asked for. See kato_webserver/cache_refresh.py.
        app = self._app()
        app.config['AGENT_VERSION_INFO'] = dict(_SAMPLE)
        with mock.patch(
            'kato_core_lib.helpers.agent_version_utils.reset_latest_version_cache',
        ) as reset:
            app.test_client().post('/api/refresh', json={'target': 'agent-version'})
        reset.assert_called_once()

    def test_the_GET_no_longer_refreshes(self):
        # The whole point of the move: a prefetch or a link preview must not be
        # able to respawn a subprocess and hit the registry.
        app = self._app()
        app.config['AGENT_VERSION_INFO'] = dict(_SAMPLE)
        with mock.patch(
            'kato_core_lib.helpers.agent_version_utils.reset_latest_version_cache',
        ) as reset:
            app.test_client().get('/api/agent-version?refresh=1')
        reset.assert_not_called()
        self.assertEqual(app.config.get('AGENT_VERSION_INFO'), dict(_SAMPLE))


_RUNNING = {
    'state': 'running', 'percent': 30, 'step': 'Downloading…',
    'command': 'npm install -g @anthropic-ai/claude-code@latest',
    'manager': 'npm', 'lines': ['npm http fetch GET 200'], 'ok': None,
    'message': '', 'version_before': '2.1.179', 'version_after': None,
}


class AgentVersionUpgradeRouteTests(unittest.TestCase):
    def _app(self):
        return create_app(session_manager=_Manager())

    def test_post_starts_the_job_and_busts_the_version_cache(self):
        app = self._app()
        app.config['AGENT_VERSION_INFO'] = dict(_SAMPLE)  # pre-cached
        with mock.patch(
            'kato_core_lib.helpers.agent_cli_upgrade_job.start',
            return_value=dict(_RUNNING),
        ) as start:
            body = app.test_client().post('/api/agent-version/upgrade').get_json()
        start.assert_called_once()
        self.assertEqual(body['state'], 'running')
        self.assertEqual(body['percent'], 30)
        self.assertIsNone(app.config.get('AGENT_VERSION_INFO'))  # re-probe next GET

    def test_post_returns_immediately_instead_of_blocking(self):
        # The old route held the request open for the whole install; the job
        # must be started, not awaited.
        app = self._app()
        with mock.patch(
            'kato_core_lib.helpers.agent_cli_upgrade_job.start',
            return_value=dict(_RUNNING),
        ):
            response = app.test_client().post('/api/agent-version/upgrade')
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.get_json()['ok'])  # still in flight

    def test_get_reports_progress(self):
        app = self._app()
        with mock.patch(
            'kato_core_lib.helpers.agent_cli_upgrade_job.status',
            return_value=dict(_RUNNING),
        ):
            body = app.test_client().get('/api/agent-version/upgrade').get_json()
        self.assertEqual(body['percent'], 30)
        self.assertEqual(body['step'], 'Downloading…')
        self.assertEqual(body['lines'], ['npm http fetch GET 200'])

    def test_finished_job_busts_the_version_cache(self):
        app = self._app()
        app.config['AGENT_VERSION_INFO'] = dict(_SAMPLE)
        with mock.patch(
            'kato_core_lib.helpers.agent_cli_upgrade_job.status',
            return_value=dict(_RUNNING, state='done', ok=True, percent=100),
        ):
            body = app.test_client().get('/api/agent-version/upgrade').get_json()
        self.assertTrue(body['ok'])
        self.assertIsNone(app.config.get('AGENT_VERSION_INFO'))

    def test_running_job_keeps_the_version_cache(self):
        app = self._app()
        app.config['AGENT_VERSION_INFO'] = dict(_SAMPLE)
        with mock.patch(
            'kato_core_lib.helpers.agent_cli_upgrade_job.status',
            return_value=dict(_RUNNING),
        ):
            app.test_client().get('/api/agent-version/upgrade')
        self.assertIsNotNone(app.config.get('AGENT_VERSION_INFO'))

    def test_upgrade_failure_reported_in_body(self):
        app = self._app()
        with mock.patch(
            'kato_core_lib.helpers.agent_cli_upgrade_job.start',
            return_value={'state': 'error', 'ok': False, 'percent': 0,
                          'message': 'in-app upgrade is disabled', 'lines': []},
        ):
            body = app.test_client().post('/api/agent-version/upgrade').get_json()
        self.assertFalse(body['ok'])
        self.assertIn('disabled', body['message'])


if __name__ == '__main__':
    unittest.main()
