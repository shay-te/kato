"""End-to-end check for the safety state endpoint that drives the red
``SafetyBanner`` in the planning UI.

The UI mounts the banner only when ``GET /api/safety`` reports
``bypass_permissions: true``. This test confirms the endpoint reflects
the env state correctly so a misconfiguration on the server side cannot
leave the banner silently hidden.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from kato_webserver.app import create_app


class SafetyEndpointTests(unittest.TestCase):
    def _client(self):
        app = create_app(
            session_manager=None,
            workspace_manager=None,
            planning_session_runner=None,
        )
        return app.test_client()

    def test_bypass_off_reports_false(self) -> None:
        with patch.dict(os.environ, {
            'KATO_CLAUDE_BYPASS_PERMISSIONS': 'false',
            'KATO_CLAUDE_BYPASS_PERMISSIONS_ACCEPT': 'false',
        }, clear=False):
            resp = self._client().get('/api/safety')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertFalse(body['bypass_permissions'])
        self.assertFalse(body['accept_acknowledged'])

    def test_bypass_on_reports_true_so_banner_renders(self) -> None:
        # The UI's <SafetyBanner /> renders iff state.bypass_permissions
        # is truthy. We're asserting the wire shape that gates the banner.
        with patch.dict(os.environ, {
            'KATO_CLAUDE_BYPASS_PERMISSIONS': 'true',
            'KATO_CLAUDE_BYPASS_PERMISSIONS_ACCEPT': 'true',
        }, clear=False):
            resp = self._client().get('/api/safety')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertTrue(body['bypass_permissions'])
        self.assertTrue(body['accept_acknowledged'])
        self.assertIn('running_as_root', body)


if __name__ == '__main__':
    unittest.main()
