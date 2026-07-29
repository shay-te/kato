"""End-to-end check for ``GET /api/credential-sources``.

This is what lets the first-run wizard offer "use my gh CLI login"
instead of demanding a pasted API token. Two properties matter on the
wire and are pinned here: the response never carries a token, and a
broken probe degrades to an empty list (the paste form is always the
fallback) rather than a 500 that would wedge the setup screen.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from kato_webserver.app import create_app


MODULE = 'kato_core_lib.helpers.credential_sources'


class CredentialSourcesEndpointTests(unittest.TestCase):
    def _client(self):
        app = create_app(
            session_manager=None,
            workspace_manager=None,
            planning_session_runner=None,
        )
        return app.test_client()

    def test_lists_discovered_sources_for_a_provider(self) -> None:
        with patch(f'{MODULE}.provider_cli_binary', return_value='gh'), \
             patch(f'{MODULE}.read_provider_cli_token', return_value='ghp_live'), \
             patch(f'{MODULE}.read_provider_cli_account', return_value='octocat'), \
             patch(f'{MODULE}.read_git_credential', return_value=('', '')):
            resp = self._client().get('/api/credential-sources?provider=github')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body['provider'], 'github')
        self.assertEqual([s['id'] for s in body['sources']], ['cli'])
        self.assertEqual(body['sources'][0]['account'], 'octocat')

    def test_the_token_never_crosses_to_the_browser(self) -> None:
        with patch(f'{MODULE}.provider_cli_binary', return_value='gh'), \
             patch(f'{MODULE}.read_provider_cli_token', return_value='SUPER-SECRET'), \
             patch(f'{MODULE}.read_provider_cli_account', return_value='octocat'), \
             patch(f'{MODULE}.read_git_credential', return_value=('u', 'ALSO-SECRET')):
            resp = self._client().get('/api/credential-sources?provider=github')
        self.assertNotIn('SUPER-SECRET', resp.get_data(as_text=True))
        self.assertNotIn('ALSO-SECRET', resp.get_data(as_text=True))

    def test_provider_is_required(self) -> None:
        resp = self._client().get('/api/credential-sources')
        self.assertEqual(resp.status_code, 400)

    def test_a_tracker_returns_an_empty_list_not_an_error(self) -> None:
        resp = self._client().get('/api/credential-sources?provider=jira')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()['sources'], [])

    def test_a_wedged_probe_degrades_to_the_paste_form(self) -> None:
        with patch(f'{MODULE}.discover_credential_sources',
                   side_effect=RuntimeError('gh exploded')):
            resp = self._client().get('/api/credential-sources?provider=github')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()['sources'], [])


class CredentialSourcePersistenceTests(unittest.TestCase):
    """Picking a source must be SAVEABLE — the provider whitelist blocks
    any key it doesn't know, which is exactly how this broke first."""

    def test_source_key_is_whitelisted_for_every_discoverable_provider(self) -> None:
        from kato_webserver.app import _GIT_HOST_FIELDS, _TASK_PROVIDER_FIELDS

        for provider in ('github', 'gitlab', 'bitbucket'):
            key = f'{provider.upper()}_API_TOKEN_SOURCE'
            self.assertIn(key, _TASK_PROVIDER_FIELDS[provider])
            self.assertIn(key, _GIT_HOST_FIELDS[provider])

    def test_trackers_do_not_gain_a_source_key(self) -> None:
        from kato_webserver.app import _TASK_PROVIDER_FIELDS

        for provider in ('jira', 'youtrack'):
            self.assertNotIn(
                f'{provider.upper()}_API_TOKEN_SOURCE',
                _TASK_PROVIDER_FIELDS[provider],
            )


if __name__ == '__main__':
    unittest.main()
