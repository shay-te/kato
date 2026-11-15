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

    def test_the_source_chosen_in_settings_wins_over_a_token_in_the_environment(self) -> None:
        # Was "the shell still wins" — reversed 2026-11-15 (a stale .env token
        # outranked the one in Settings): "no .env, only kato settings".
        from kato_core_lib.helpers import kato_settings_store_utils

        with patch.object(kato_settings_store_utils, 'read_kato_settings',
                          return_value={'GITHUB_API_TOKEN_SOURCE': SOURCE_CLI}), \
             patch(f'{MODULE}.read_provider_cli_token', return_value='ghp_live'), \
             patch.dict('os.environ', {'GITHUB_API_TOKEN': 'from-shell'}, clear=True):
            env = kato_settings_store_utils.effective_config_env()
        self.assertEqual(env.get('GITHUB_API_TOKEN'), 'ghp_live')

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



class BootResolvesCredentialSourcesTests(unittest.TestCase):
    """KAN-1, 2026-11-15: GitHub set to "use my git login". Test connection
    resolved the source and said Connected; the boot exported settings.json as
    it was, so the clients had no token and every pull request failed with
    "missing pull request API token". Then, the same day: an old Bitbucket
    token from a stale .env in the terminal outranked the new one saved in
    Settings. Settings win now — "no .env, only kato settings"."""

    def setUp(self) -> None:
        credential_sources.clear_cache()
        self.addCleanup(credential_sources.clear_cache)

    def _boot(self, settings, environ=None, token='gho_from_keychain'):
        import os
        from kato_core_lib.helpers import kato_settings_store_utils as store

        with patch.object(store, 'read_kato_settings', return_value=dict(settings)), \
             patch(f'{MODULE}.read_git_credential', return_value=('shay', token)) as asked, \
             patch.dict('os.environ', dict(environ or {}), clear=True):
            changed = store.load_kato_settings_into_environ()
            return changed, os.environ.get('GITHUB_API_TOKEN'), asked

    def test_a_git_login_source_becomes_the_token_the_clients_read(self) -> None:
        from omegaconf import OmegaConf
        import os
        from kato_core_lib.helpers import kato_settings_store_utils as store

        # Config is built from interpolations BEFORE the boot fills the env,
        # exactly like hydra's — it must still read the resolved token.
        cfg = OmegaConf.create({'github': {'token': '${oc.env:GITHUB_API_TOKEN,""}'}})
        with patch.object(store, 'read_kato_settings',
                          return_value={'GITHUB_API_TOKEN_SOURCE': SOURCE_GIT_CREDENTIAL, 'GITHUB_API_TOKEN': ''}), \
             patch(f'{MODULE}.read_git_credential', return_value=('shay', 'gho_from_keychain')), \
             patch.dict('os.environ', {}, clear=True):
            self.assertIn('GITHUB_API_TOKEN', store.load_kato_settings_into_environ())
            self.assertEqual(cfg.github.token, 'gho_from_keychain')
            self.assertEqual(os.environ['GITHUB_API_TOKEN'], 'gho_from_keychain')

    def test_the_source_chosen_in_settings_replaces_a_token_the_environment_carries(self) -> None:
        changed, token, _asked = self._boot(
            {'GITHUB_API_TOKEN_SOURCE': SOURCE_GIT_CREDENTIAL},
            environ={'GITHUB_API_TOKEN': 'stale_from_dotenv'},
        )
        self.assertEqual(token, 'gho_from_keychain')
        self.assertIn('GITHUB_API_TOKEN', changed)

    def test_a_chosen_source_with_nothing_stored_leaves_no_inherited_token(self) -> None:
        # A wrong token answering 401 is worse than "add a token in Settings".
        changed, token, _asked = self._boot(
            {'GITHUB_API_TOKEN_SOURCE': SOURCE_GIT_CREDENTIAL},
            environ={'GITHUB_API_TOKEN': 'stale_from_dotenv'}, token='',
        )
        self.assertIsNone(token)
        self.assertIn('GITHUB_API_TOKEN', changed)

    def test_a_token_pasted_in_settings_wins_over_the_environment(self) -> None:
        changed, token, asked = self._boot(
            {'GITHUB_API_TOKEN': 'pasted_in_settings'}, environ={'GITHUB_API_TOKEN': 'stale_from_dotenv'},
        )
        self.assertEqual((changed, token), (['GITHUB_API_TOKEN'], 'pasted_in_settings'))
        asked.assert_not_called()

    def test_the_environment_still_fills_what_settings_leave_empty(self) -> None:
        # A Docker deployment, a test: no settings file, the env is the config.
        changed, token, _asked = self._boot({'GITHUB_API_TOKEN': ''}, environ={'GITHUB_API_TOKEN': 'from_env'})
        self.assertEqual((changed, token), ([], 'from_env'))

    def test_a_source_set_in_the_shell_counts_when_settings_have_none(self) -> None:
        changed, token, _asked = self._boot({}, environ={'GITHUB_API_TOKEN_SOURCE': SOURCE_GIT_CREDENTIAL})
        self.assertEqual((changed, token), (['GITHUB_API_TOKEN'], 'gho_from_keychain'))

    def test_a_broken_resolver_never_breaks_the_boot(self) -> None:
        import os
        from kato_core_lib.helpers import kato_settings_store_utils as store

        with patch.object(store, 'read_kato_settings',
                          return_value={'GITHUB_API_TOKEN_SOURCE': SOURCE_CLI, 'GITHUB_OWNER': 'shay-te'}), \
             patch(f'{MODULE}.resolved_credential_env', side_effect=RuntimeError('boom')), \
             patch.dict('os.environ', {}, clear=True):
            store.load_kato_settings_into_environ()
            self.assertEqual(os.environ.get('GITHUB_OWNER'), 'shay-te')

    def test_every_saved_value_wins_and_an_empty_one_is_unset(self) -> None:
        import os
        from kato_core_lib.helpers import kato_settings_store_utils as store

        with patch.object(store, 'read_kato_settings',
                          return_value={'GITHUB_OWNER': 'shay-te', 'JIRA_ISSUE_STATES': ''}), \
             patch.dict('os.environ', {'GITHUB_OWNER': 'someone-else', 'JIRA_ISSUE_STATES': ''}, clear=True):
            self.assertEqual(store.load_kato_settings_into_environ(), ['GITHUB_OWNER'])
            self.assertEqual(os.environ['GITHUB_OWNER'], 'shay-te')
            self.assertEqual(os.environ['JIRA_ISSUE_STATES'], '')  # the shell's own blank is left alone

    def test_test_connection_and_the_boot_use_the_same_rule(self) -> None:
        import os
        from kato_core_lib.helpers import kato_settings_store_utils as store

        saved = {'BITBUCKET_API_TOKEN': 'new_in_settings', 'BITBUCKET_API_EMAIL': 'me@example.com'}
        with patch.object(store, 'read_kato_settings', return_value=dict(saved)), \
             patch.dict('os.environ', {'BITBUCKET_API_TOKEN': 'stale_from_dotenv'}, clear=True):
            tested = store.config_env_with_form({})
            store.load_kato_settings_into_environ()
            self.assertEqual(tested['BITBUCKET_API_TOKEN'], os.environ['BITBUCKET_API_TOKEN'])
            self.assertEqual(tested['BITBUCKET_API_TOKEN'], 'new_in_settings')
            # A field typed on the form, before Save, wins for the test alone.
            self.assertEqual(store.config_env_with_form({'BITBUCKET_API_TOKEN': 'typed'})['BITBUCKET_API_TOKEN'], 'typed')
            # A field cleared on the form is unset — neither saved nor inherited.
            self.assertNotIn('BITBUCKET_API_EMAIL', store.config_env_with_form({'BITBUCKET_API_EMAIL': ''}))

    def test_main_loads_settings_before_it_checks_or_builds_anything(self) -> None:
        import inspect
        from kato_core_lib import main as main_module

        source = inspect.getsource(main_module.main)
        self.assertLess(source.index('load_kato_settings_into_environ()'), source.index('collect_config_errors('))
        self.assertLess(source.index('load_kato_settings_into_environ()'), source.index('KatoInstance.init('))

if __name__ == '__main__':
    unittest.main()
