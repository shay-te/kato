"""Coverage for ``kato_core_lib/helpers/credential_sources.py``.

The ladder that lets a first-comer connect a code host WITHOUT minting
and pasting an API token: reuse the ``gh``/``glab`` login, git's own
credential helper, or a conventional env var. What gets persisted is the
SOURCE name, never the secret — these tests pin that invariant hardest,
because a regression there would start writing tokens to disk.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from kato_core_lib.helpers import credential_sources
from kato_core_lib.helpers.credential_sources import (
    SOURCE_CLI,
    SOURCE_ENVIRONMENT,
    SOURCE_GIT_CREDENTIAL,
    SOURCE_PASTED,
    base_url_key,
    clear_cache,
    discover_credential_sources,
    host_for_provider,
    resolve_credential_token,
    resolved_credential_env,
    source_key,
    token_key,
)


MODULE = 'kato_core_lib.helpers.credential_sources'


class SettingsKeyNameTests(unittest.TestCase):
    def test_key_names_match_katos_env_conventions(self) -> None:
        self.assertEqual(token_key('github'), 'GITHUB_API_TOKEN')
        self.assertEqual(source_key('github'), 'GITHUB_API_TOKEN_SOURCE')
        self.assertEqual(base_url_key('gitlab'), 'GITLAB_API_BASE_URL')
        self.assertEqual(token_key(' BitBucket '), 'BITBUCKET_API_TOKEN')


class HostForProviderTests(unittest.TestCase):
    def test_public_hosts_by_default(self) -> None:
        self.assertEqual(host_for_provider('github'), 'github.com')
        self.assertEqual(host_for_provider('gitlab'), 'gitlab.com')
        self.assertEqual(host_for_provider('bitbucket'), 'bitbucket.org')

    def test_api_subdomain_is_stripped(self) -> None:
        # The credential helper and `gh` are keyed by the WEB host.
        self.assertEqual(
            host_for_provider('github', 'https://api.github.com'), 'github.com',
        )

    def test_self_hosted_base_url_wins(self) -> None:
        self.assertEqual(
            host_for_provider('gitlab', 'https://git.acme.io/api/v4'), 'git.acme.io',
        )

    def test_unknown_provider_without_base_url(self) -> None:
        self.assertEqual(host_for_provider('jira'), '')


class ResolveCredentialTokenTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_cache()
        self.addCleanup(clear_cache)

    def test_cli_source(self) -> None:
        with patch(f'{MODULE}.read_provider_cli_token', return_value='ghp_live') as read:
            self.assertEqual(resolve_credential_token('github', SOURCE_CLI), 'ghp_live')
        read.assert_called_once_with('github', 'github.com')

    def test_git_credential_source_returns_only_the_secret(self) -> None:
        with patch(f'{MODULE}.read_git_credential', return_value=('octocat', 's3cret')):
            self.assertEqual(
                resolve_credential_token('github', SOURCE_GIT_CREDENTIAL), 's3cret',
            )

    def test_environment_source(self) -> None:
        with patch.dict('os.environ', {'GH_TOKEN': 'from-env'}, clear=False):
            self.assertEqual(
                resolve_credential_token('github', SOURCE_ENVIRONMENT), 'from-env',
            )

    def test_environment_source_prefers_gh_token_over_github_token(self) -> None:
        with patch.dict('os.environ',
                        {'GH_TOKEN': 'first', 'GITHUB_TOKEN': 'second'}, clear=False):
            self.assertEqual(
                resolve_credential_token('github', SOURCE_ENVIRONMENT), 'first',
            )

    def test_pasted_and_unknown_sources_resolve_to_nothing(self) -> None:
        # The caller then falls back to the stored (pasted) value.
        for source in (SOURCE_PASTED, '', 'nonsense'):
            self.assertEqual(resolve_credential_token('github', source), '')

    def test_self_hosted_host_is_passed_through(self) -> None:
        with patch(f'{MODULE}.read_provider_cli_token', return_value='t') as read:
            resolve_credential_token('gitlab', SOURCE_CLI, 'https://git.acme.io/api/v4')
        read.assert_called_once_with('gitlab', 'git.acme.io')

    def test_result_is_cached_so_ui_polling_does_not_spawn_processes(self) -> None:
        with patch(f'{MODULE}.read_provider_cli_token', return_value='ghp') as read:
            for _ in range(5):
                resolve_credential_token('github', SOURCE_CLI)
        read.assert_called_once()

    def test_clear_cache_forces_a_re_probe(self) -> None:
        with patch(f'{MODULE}.read_provider_cli_token', return_value='ghp') as read:
            resolve_credential_token('github', SOURCE_CLI)
            clear_cache()
            resolve_credential_token('github', SOURCE_CLI)
        self.assertEqual(read.call_count, 2)

    def test_environment_source_is_never_cached(self) -> None:
        # Free to read, and the operator can change it between polls.
        with patch.dict('os.environ', {'GH_TOKEN': 'one'}, clear=False):
            self.assertEqual(resolve_credential_token('github', SOURCE_ENVIRONMENT), 'one')
        with patch.dict('os.environ', {'GH_TOKEN': 'two'}, clear=False):
            self.assertEqual(resolve_credential_token('github', SOURCE_ENVIRONMENT), 'two')


class DiscoverCredentialSourcesTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_cache()
        self.addCleanup(clear_cache)

    def test_lists_every_available_source_cheapest_first(self) -> None:
        with patch(f'{MODULE}.provider_cli_binary', return_value='gh'), \
             patch(f'{MODULE}.read_provider_cli_token', return_value='ghp'), \
             patch(f'{MODULE}.read_provider_cli_account', return_value='octocat'), \
             patch(f'{MODULE}.read_git_credential', return_value=('octocat', 'stored')), \
             patch.dict('os.environ', {'GH_TOKEN': 'env-token'}, clear=False):
            sources = discover_credential_sources('github')
        self.assertEqual(
            [item['id'] for item in sources],
            [SOURCE_CLI, SOURCE_GIT_CREDENTIAL, SOURCE_ENVIRONMENT],
        )
        self.assertEqual(sources[0]['label'], 'gh CLI login')
        self.assertEqual(sources[0]['account'], 'octocat')
        self.assertIn('octocat', sources[0]['detail'])

    def test_never_returns_the_token_itself(self) -> None:
        # This crosses to the browser — a leak here would put a live
        # token in the DOM.
        with patch(f'{MODULE}.provider_cli_binary', return_value='gh'), \
             patch(f'{MODULE}.read_provider_cli_token', return_value='SECRET-TOKEN'), \
             patch(f'{MODULE}.read_provider_cli_account', return_value='octocat'), \
             patch(f'{MODULE}.read_git_credential', return_value=('octocat', 'OTHER-SECRET')), \
             patch.dict('os.environ', {'GH_TOKEN': 'ENV-SECRET'}, clear=False):
            sources = discover_credential_sources('github')
        blob = repr(sources)
        for secret in ('SECRET-TOKEN', 'OTHER-SECRET', 'ENV-SECRET'):
            self.assertNotIn(secret, blob)

    def test_signed_out_cli_is_not_offered(self) -> None:
        with patch(f'{MODULE}.provider_cli_binary', return_value='gh'), \
             patch(f'{MODULE}.read_provider_cli_token', return_value=''), \
             patch(f'{MODULE}.read_git_credential', return_value=('', '')), \
             patch.dict('os.environ', {}, clear=True):
            self.assertEqual(discover_credential_sources('github'), [])

    def test_git_credential_without_a_username(self) -> None:
        with patch(f'{MODULE}.provider_cli_binary', return_value=''), \
             patch(f'{MODULE}.read_git_credential', return_value=('', 'stored')), \
             patch.dict('os.environ', {}, clear=True):
            sources = discover_credential_sources('bitbucket')
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]['account'], '')
        self.assertIn('bitbucket.org', sources[0]['detail'])

    def test_trackers_have_nothing_to_discover(self) -> None:
        # Jira / YouTrack have no CLI and no git credential — the UI
        # falls back to the paste form rather than showing an empty box.
        for provider in ('jira', 'youtrack', '', 'nonsense'):
            self.assertEqual(discover_credential_sources(provider), [])


class ResolvedCredentialEnvTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_cache()
        self.addCleanup(clear_cache)

    def test_fills_the_token_for_a_provider_using_a_source(self) -> None:
        with patch(f'{MODULE}.read_provider_cli_token', return_value='ghp_live'):
            resolved = resolved_credential_env({'GITHUB_API_TOKEN_SOURCE': SOURCE_CLI})
        self.assertEqual(resolved, {'GITHUB_API_TOKEN': 'ghp_live'})

    def test_an_explicitly_pasted_token_still_wins(self) -> None:
        with patch(f'{MODULE}.read_provider_cli_token', return_value='ghp_live') as read:
            resolved = resolved_credential_env({
                'GITHUB_API_TOKEN_SOURCE': SOURCE_CLI,
                'GITHUB_API_TOKEN': 'pasted-by-hand',
            })
        self.assertEqual(resolved, {})
        read.assert_not_called()

    def test_no_source_configured_is_a_no_op(self) -> None:
        with patch(f'{MODULE}.read_provider_cli_token') as read:
            self.assertEqual(resolved_credential_env({'GITHUB_API_TOKEN': ''}), {})
        read.assert_not_called()

    def test_pasted_source_marker_is_a_no_op(self) -> None:
        with patch(f'{MODULE}.read_provider_cli_token') as read:
            self.assertEqual(
                resolved_credential_env({'GITHUB_API_TOKEN_SOURCE': SOURCE_PASTED}), {},
            )
        read.assert_not_called()

    def test_a_source_that_went_away_yields_no_key(self) -> None:
        # Signed out of gh since setup: kato must report a MISSING
        # credential rather than boot with a silently empty token.
        with patch(f'{MODULE}.read_provider_cli_token', return_value=''):
            self.assertEqual(
                resolved_credential_env({'GITHUB_API_TOKEN_SOURCE': SOURCE_CLI}), {},
            )

    def test_several_providers_at_once(self) -> None:
        with patch(f'{MODULE}.read_provider_cli_token', return_value='cli-token'), \
             patch(f'{MODULE}.read_git_credential', return_value=('u', 'git-token')):
            resolved = resolved_credential_env({
                'GITHUB_API_TOKEN_SOURCE': SOURCE_CLI,
                'BITBUCKET_API_TOKEN_SOURCE': SOURCE_GIT_CREDENTIAL,
            })
        self.assertEqual(resolved, {
            'GITHUB_API_TOKEN': 'cli-token',
            'BITBUCKET_API_TOKEN': 'git-token',
        })

    def test_empty_and_none_settings(self) -> None:
        self.assertEqual(resolved_credential_env({}), {})
        self.assertEqual(resolved_credential_env(None), {})

    def test_self_hosted_base_url_reaches_the_probe(self) -> None:
        with patch(f'{MODULE}.read_provider_cli_token', return_value='t') as read:
            resolved_credential_env({
                'GITLAB_API_TOKEN_SOURCE': SOURCE_CLI,
                'GITLAB_API_BASE_URL': 'https://git.acme.io/api/v4',
            })
        read.assert_called_once_with('gitlab', 'git.acme.io')


class SettingsStoreIntegrationTests(unittest.TestCase):
    """The store is the single choke point where a source becomes a token."""

    def setUp(self) -> None:
        clear_cache()
        self.addCleanup(clear_cache)

    def test_effective_config_env_resolves_a_source(self) -> None:
        from kato_core_lib.helpers import kato_settings_store_utils

        with patch.object(kato_settings_store_utils, 'read_kato_settings',
                          return_value={'GITHUB_API_TOKEN_SOURCE': SOURCE_CLI}), \
             patch(f'{MODULE}.read_provider_cli_token', return_value='ghp_live'), \
             patch.dict('os.environ', {}, clear=True):
            env = kato_settings_store_utils.effective_config_env()
        self.assertEqual(env.get('GITHUB_API_TOKEN'), 'ghp_live')

    def test_shell_env_still_wins_over_a_discovered_token(self) -> None:
        from kato_core_lib.helpers import kato_settings_store_utils

        with patch.object(kato_settings_store_utils, 'read_kato_settings',
                          return_value={'GITHUB_API_TOKEN_SOURCE': SOURCE_CLI}), \
             patch(f'{MODULE}.read_provider_cli_token', return_value='ghp_live'), \
             patch.dict('os.environ', {'GITHUB_API_TOKEN': 'from-shell'}, clear=True):
            env = kato_settings_store_utils.effective_config_env()
        self.assertEqual(env.get('GITHUB_API_TOKEN'), 'from-shell')

    def test_discovery_failure_never_breaks_config_load(self) -> None:
        from kato_core_lib.helpers import kato_settings_store_utils

        with patch.object(kato_settings_store_utils, 'read_kato_settings',
                          return_value={'REPOSITORY_ROOT_PATH': '/repos'}), \
             patch(f'{MODULE}.resolved_credential_env', side_effect=RuntimeError('boom')), \
             patch.dict('os.environ', {}, clear=True):
            env = kato_settings_store_utils.effective_config_env()
        self.assertEqual(env.get('REPOSITORY_ROOT_PATH'), '/repos')

    def test_boot_loader_injects_the_resolved_token(self) -> None:
        from kato_core_lib.helpers import kato_settings_store_utils

        with patch.object(kato_settings_store_utils, 'read_kato_settings',
                          return_value={'GITHUB_API_TOKEN_SOURCE': SOURCE_CLI}), \
             patch(f'{MODULE}.read_provider_cli_token', return_value='ghp_live'), \
             patch.dict('os.environ', {}, clear=True):
            kato_settings_store_utils.load_kato_settings_into_environ()
            import os
            self.assertEqual(os.environ.get('GITHUB_API_TOKEN'), 'ghp_live')


if __name__ == '__main__':
    unittest.main()
