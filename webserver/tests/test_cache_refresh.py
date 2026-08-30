"""``POST /api/refresh`` — dropping a discovery cache is an ACTION, not a read.

Three refreshes used to ride ``?refresh=1`` on their own GET routes. Each
spawns a CLI subprocess, calls the npm registry or the models API, and — for
the backend probe — clears a cache global to the whole server process.

Not a security fix: every kato route, GET included, already sits behind the
same origin guard. The problem is that browsers issue GETs nobody asked for
(Chrome prefetches links on hover), so a verb promising "this only reads"
should not be the one that respawns a subprocess.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from kato_webserver.app import create_app
from kato_webserver.cache_refresh import TARGET_NAMES, refresh_target


class _FakeManager:
    def get_session(self, task_id):  # noqa: ARG002
        return None

    def list_records(self):
        return []


class RefreshRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_app(session_manager=_FakeManager())
        self.client = self.app.test_client()

    def test_every_target_is_accepted(self) -> None:
        for target in TARGET_NAMES:
            with self.subTest(target=target), patch(
                'kato_core_lib.helpers.agent_version_utils.reset_latest_version_cache',
            ), patch(
                'kato_core_lib.helpers.agent_backend_readiness.reset_probe_cache',
            ), patch(
                'agent_backend_core_lib.agent_backend_core_lib.client'
                '.model_catalog_factory.discover_models',
                return_value=[],
            ):
                response = self.client.post('/api/refresh', json={'target': target})
                self.assertEqual(response.status_code, 200)

    def test_an_unknown_target_is_a_400_and_names_the_valid_ones(self) -> None:
        response = self.client.post('/api/refresh', json={'target': 'nope'})
        self.assertEqual(response.status_code, 400)
        error = response.get_json()['error']
        self.assertIn('nope', error)
        for target in TARGET_NAMES:
            self.assertIn(target, error)

    def test_a_missing_target_is_a_400(self) -> None:
        self.assertEqual(
            self.client.post('/api/refresh', json={}).status_code, 400,
        )

    def test_it_is_not_reachable_by_GET(self) -> None:
        # The whole point: a browser cannot stumble into it.
        self.assertEqual(self.client.get('/api/refresh').status_code, 405)

    def test_a_failing_refresh_reports_500_rather_than_raising(self) -> None:
        # Patches what the refresher CALLS, not the refresher — the dispatch
        # table binds the functions at import, and a stub in its place would
        # test the stub rather than the real path.
        with patch(
            'agent_backend_core_lib.agent_backend_core_lib.client'
            '.model_catalog_factory.discover_models',
            side_effect=RuntimeError('probe exploded'),
        ):
            response = self.client.post('/api/refresh', json={'target': 'models'})
        self.assertEqual(response.status_code, 500)
        self.assertIn('probe exploded', response.get_json()['error'])


class RefreshTargetTests(unittest.TestCase):
    """The dispatch itself, without the route."""

    def setUp(self) -> None:
        self.app = create_app(session_manager=_FakeManager())

    def test_unknown_target_reports_rather_than_raises(self) -> None:
        ok, error = refresh_target(self.app, 'not-a-target')
        self.assertFalse(ok)
        self.assertIn('not-a-target', error)

    def test_a_raising_refresher_is_caught(self) -> None:
        # A stale cache is a far smaller problem than a 500 on the button that
        # exists to unstick things.
        with patch(
            'kato_core_lib.helpers.agent_backend_readiness.reset_probe_cache',
            side_effect=OSError('no such binary'),
        ):
            ok, error = refresh_target(self.app, 'agent-backends')
        self.assertFalse(ok)
        self.assertIn('no such binary', error)

    def test_agent_version_drops_EVERY_per_backend_entry(self) -> None:
        # The probe is cached per backend, so clearing only the unscoped key
        # would leave the tab the operator is looking at stale.
        self.app.config['AGENT_VERSION_INFO'] = {'version': 'old'}
        self.app.config['AGENT_VERSION_INFO::claude'] = {'version': 'old'}
        self.app.config['AGENT_VERSION_INFO::codex'] = {'version': 'old'}

        with patch(
            'kato_core_lib.helpers.agent_version_utils.reset_latest_version_cache',
        ):
            ok, _ = refresh_target(self.app, 'agent-version')

        self.assertTrue(ok)
        self.assertNotIn('AGENT_VERSION_INFO', self.app.config)
        self.assertNotIn('AGENT_VERSION_INFO::claude', self.app.config)
        self.assertNotIn('AGENT_VERSION_INFO::codex', self.app.config)

    def test_agent_version_also_reasks_the_registry(self) -> None:
        # Otherwise a release published during this process's lifetime stays
        # invisible until the TTL lapses — the exact case Refresh is for.
        with patch(
            'kato_core_lib.helpers.agent_version_utils.reset_latest_version_cache',
        ) as reset:
            refresh_target(self.app, 'agent-version')
        reset.assert_called_once()


class RefreshIsNoLongerOnTheGetsTests(unittest.TestCase):
    """``?refresh=1`` must not still work — otherwise nothing was fixed."""

    def setUp(self) -> None:
        self.app = create_app(session_manager=_FakeManager())
        self.client = self.app.test_client()

    def test_agent_version_get_ignores_refresh(self) -> None:
        self.app.config['AGENT_VERSION_INFO'] = {'version': 'cached'}
        self.client.get('/api/agent-version?refresh=1')
        self.assertEqual(
            self.app.config.get('AGENT_VERSION_INFO'), {'version': 'cached'},
        )

    def test_agent_backends_get_does_not_clear_the_global_probe(self) -> None:
        with patch(
            'kato_core_lib.helpers.agent_backend_readiness.reset_probe_cache',
        ) as reset:
            self.client.get('/api/agent-backends?refresh=1')
        reset.assert_not_called()


if __name__ == '__main__':
    unittest.main()
