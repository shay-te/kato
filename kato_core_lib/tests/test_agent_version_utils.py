import json
import unittest
from unittest import mock

from kato_core_lib.helpers import agent_version_utils as avu

# A resolved path that looks like an npm global install — ``_is_npm_managed``
# keys off ``node_modules`` in the REAL path, so tests must say which kind of
# install they are describing instead of leaving it to the host.
_NPM_REAL_PATH = '/usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude'
_NATIVE_REAL_PATH = '/home/op/.local/share/claude/versions/2.1.0/claude'


def _npm_managed(real_path=_NPM_REAL_PATH):
    """Pin how the CLI on PATH resolves, so the upgrade plan is deterministic."""
    return mock.patch.object(avu.os.path, 'realpath', return_value=real_path)


def _registry(version=None, node=''):
    """Stub the module's ONE network entry point and clear its cache.

    Everything that consults the registry — the update check AND the upgrade
    plan's Node-engine gate — goes through ``_fetch_latest_package``, so
    patching it here is what keeps the suite off the network.
    """
    avu.reset_latest_version_cache()
    return mock.patch.object(
        avu, '_fetch_latest_package',
        return_value={'version': version, 'node': node},
    )


def _info(env, version_output='2.1.142 (Claude Code)', on_path=True,
          latest=None, real_path=_NPM_REAL_PATH, node=''):
    """``agent_version_info`` with the host probes and the registry stubbed out.

    ``latest`` defaults to "unknown" so no test touches the npm registry —
    a test that cares about the published version passes one explicitly.
    """
    which = mock.patch.object(
        avu.shutil, 'which',
        return_value=('/usr/local/bin/agent' if on_path else None),
    )
    with which, _npm_managed(real_path), _registry(latest, node):
        try:
            return avu.agent_version_info(
                env=env, runner=lambda path: version_output,
                latest=lambda backend: latest,
            )
        finally:
            avu.reset_latest_version_cache()


class ParseVersionTests(unittest.TestCase):
    def test_parses_with_and_without_prefix(self):
        self.assertEqual(avu.parse_version('2.1.142 (Claude Code)'), (2, 1, 142))
        self.assertEqual(avu.parse_version('codex 0.40.0'), (0, 40, 0))

    def test_none_when_no_version(self):
        self.assertIsNone(avu.parse_version('no version here'))
        self.assertIsNone(avu.parse_version(None))


class ClaudeBackendTests(unittest.TestCase):
    def test_old_version_is_out_of_date_and_no_workflows(self):
        info = _info({'KATO_AGENT_BACKEND': 'claude'}, '2.1.142')
        self.assertEqual(info['backend'], 'claude')
        self.assertEqual(info['version'], '2.1.142')
        self.assertFalse(info['up_to_date'])
        self.assertFalse(info['supports_workflows'])

    def test_new_version_is_up_to_date_and_supports_workflows(self):
        info = _info({'KATO_AGENT_BACKEND': 'claude'}, '2.1.170')
        self.assertTrue(info['up_to_date'])
        self.assertTrue(info['supports_workflows'])

    def test_min_version_env_override(self):
        info = _info(
            {'KATO_AGENT_BACKEND': 'claude', 'KATO_CLAUDE_MIN_VERSION': '2.1.100'},
            '2.1.142',
        )
        self.assertTrue(info['up_to_date'])
        self.assertTrue(info['supports_workflows'])

    def test_binary_missing(self):
        info = _info({'KATO_AGENT_BACKEND': 'claude'}, on_path=False)
        self.assertFalse(info['found'])
        self.assertFalse(info['up_to_date'])
        self.assertIn('not found', info['detail'])

    def test_unparseable_version_does_not_false_alarm(self):
        info = _info({'KATO_AGENT_BACKEND': 'claude'}, 'weird-build-xyz')
        self.assertIsNone(info['version'])
        self.assertTrue(info['up_to_date'])          # don't nag on unknown
        self.assertFalse(info['supports_workflows'])  # but don't claim support

    def test_alias_resolves_to_claude(self):
        info = _info({'KATO_AGENT_BACKEND': 'claude-code'}, '2.1.142')
        self.assertEqual(info['backend'], 'claude')

    def test_download_url_default_and_override(self):
        info = _info({'KATO_AGENT_BACKEND': 'claude'}, '2.1.142')
        self.assertIn('claude.com', info['download_url'])
        info2 = _info(
            {'KATO_AGENT_BACKEND': 'claude', 'KATO_CLAUDE_DOWNLOAD_URL': 'https://x/y'},
            '2.1.142',
        )
        self.assertEqual(info2['download_url'], 'https://x/y')


class CodexBackendTests(unittest.TestCase):
    def test_no_min_means_not_flagged_but_workflows_off(self):
        info = _info({'KATO_AGENT_BACKEND': 'codex'}, 'codex 0.40.0')
        self.assertEqual(info['backend'], 'codex')
        self.assertEqual(info['version'], '0.40.0')
        self.assertTrue(info['up_to_date'])           # no default gate for codex
        self.assertFalse(info['supports_workflows'])  # ultracode is claude-only
        self.assertIn('openai.com', info['download_url'])  # codex download page

    def test_min_override_flags_out_of_date(self):
        info = _info(
            {'KATO_AGENT_BACKEND': 'codex', 'KATO_CODEX_MIN_VERSION': '0.50.0'},
            'codex 0.40.0',
        )
        self.assertFalse(info['up_to_date'])

    def test_uses_codex_binary_env(self):
        with mock.patch.object(avu.shutil, 'which', return_value='/x/codex') as which:
            avu.agent_version_info(
                env={'KATO_AGENT_BACKEND': 'codex', 'KATO_CODEX_BINARY': 'mycodex'},
                runner=lambda path: 'codex 1.2.3',
            )
        which.assert_any_call('mycodex')


class OpenHandsBackendTests(unittest.TestCase):
    def test_openhands_is_not_a_cli_version_check(self):
        info = avu.agent_version_info(env={'KATO_AGENT_BACKEND': 'openhands'})
        self.assertEqual(info['backend'], 'openhands')
        self.assertTrue(info['up_to_date'])
        self.assertFalse(info['supports_workflows'])
        self.assertIn('server', info['detail'])

    def test_empty_backend_defaults_to_openhands(self):
        self.assertEqual(
            avu.agent_version_info(env={})['backend'], 'openhands',
        )


class UpgradeTests(unittest.TestCase):
    ENABLED = {'KATO_AGENT_BACKEND': 'claude', 'KATO_ALLOW_CLI_UPGRADE': 'true'}

    def setUp(self):
        # ``upgrade_agent_cli`` plans before it runs, and planning consults the
        # registry for the Node-engine gate — stub it so nothing reaches out.
        registry = _registry()
        registry.start()
        self.addCleanup(registry.stop)
        self.addCleanup(avu.reset_latest_version_cache)

    def test_gating(self):
        # On by default for the claude CLI on the host — no flag needed.
        self.assertTrue(avu.upgrade_allowed({'KATO_AGENT_BACKEND': 'claude'})[0])
        self.assertTrue(avu.upgrade_allowed(self.ENABLED)[0])
        # Hard off-switch: explicit falsy disables.
        self.assertFalse(avu.upgrade_allowed(
            {'KATO_AGENT_BACKEND': 'claude', 'KATO_ALLOW_CLI_UPGRADE': 'false'})[0])
        # codex ships as an npm package too, so it is upgradable in-app.
        self.assertTrue(avu.upgrade_allowed({'KATO_AGENT_BACKEND': 'codex'})[0])
        # OpenHands has no local CLI to upgrade; never in Docker (the CLI
        # lives in the image).
        self.assertFalse(avu.upgrade_allowed({'KATO_AGENT_BACKEND': 'openhands'})[0])
        self.assertFalse(avu.upgrade_allowed(
            {**self.ENABLED, 'KATO_CLAUDE_DOCKER': 'true'})[0])

    def test_can_upgrade_flag_in_info(self):
        info = _info(self.ENABLED, '2.1.142')
        self.assertTrue(info['can_upgrade'])
        self.assertEqual(
            info['upgrade_command'], 'npm install -g @anthropic-ai/claude-code@latest',
        )

    def test_can_upgrade_on_by_default_for_claude(self):
        # No KATO_ALLOW_CLI_UPGRADE set → still offered (claude + outdated).
        self.assertTrue(
            _info({'KATO_AGENT_BACKEND': 'claude'}, '2.1.142')['can_upgrade'])

    def test_can_upgrade_false_when_up_to_date_or_hard_disabled(self):
        self.assertFalse(_info(self.ENABLED, '2.1.170')['can_upgrade'])
        self.assertFalse(_info(
            {'KATO_AGENT_BACKEND': 'claude', 'KATO_ALLOW_CLI_UPGRADE': 'false'},
            '2.1.142')['can_upgrade'])

    # ----- "behind the published release" is separate from "below the floor" -----

    def test_a_newer_published_release_offers_the_upgrade_above_the_floor(self):
        # The reported bug: on 2.1.179 with a floor of 2.1.160, the app said
        # "up to date" and hid the button while 2.1.222 was published.
        info = _info(self.ENABLED, '2.1.179', latest='2.1.222')
        self.assertTrue(info['up_to_date'])          # clears the recommended floor…
        self.assertTrue(info['update_available'])    # …but is behind what's published
        self.assertEqual(info['latest_version'], '2.1.222')
        self.assertTrue(info['can_upgrade'])

    def test_matching_the_published_release_is_not_an_update(self):
        info = _info(self.ENABLED, '2.1.222', latest='2.1.222')
        self.assertFalse(info['update_available'])
        self.assertFalse(info['can_upgrade'])

    def test_unknown_published_version_never_nags(self):
        # Offline / registry down → no claim either way, and no button.
        info = _info(self.ENABLED, '2.1.222', latest=None)
        self.assertIsNone(info['latest_version'])
        self.assertFalse(info['update_available'])
        self.assertFalse(info['can_upgrade'])

    def test_unparseable_local_version_does_not_claim_an_update(self):
        info = _info(self.ENABLED, 'weird-build-xyz', latest='2.1.222')
        self.assertEqual(info['latest_version'], '2.1.222')
        self.assertFalse(info['update_available'])

    def test_published_version_is_cached_then_resettable(self):
        calls = {'n': 0}

        def fetcher(package):
            calls['n'] += 1
            return {'version': '9.9.9', 'node': ''}

        avu.reset_latest_version_cache()
        self.addCleanup(avu.reset_latest_version_cache)
        self.assertEqual(avu.latest_published_version('claude', fetcher), '9.9.9')
        self.assertEqual(avu.latest_published_version('claude', fetcher), '9.9.9')
        self.assertEqual(calls['n'], 1)          # second read served from cache
        avu.reset_latest_version_cache()
        self.assertEqual(avu.latest_published_version('claude', fetcher), '9.9.9')
        self.assertEqual(calls['n'], 2)          # reset forces a re-check

    def test_published_version_unknown_for_a_backend_without_a_package(self):
        self.assertIsNone(
            avu.latest_published_version('openhands', lambda p: {'version': '1.0.0'}))

class RegistryFetchTests(unittest.TestCase):
    """The one network entry point. Its own class so the class-wide stub
    other suites install does not shadow the function under test."""

    def test_registry_failure_is_swallowed(self):
        with mock.patch.object(avu.urllib.request, 'urlopen', side_effect=OSError('down')):
            self.assertEqual(
                avu._fetch_latest_package('@anthropic-ai/claude-code'),
                {'version': None, 'node': ''},
            )

    def test_registry_metadata_carries_the_node_engine(self):
        payload = json.dumps(
            {'version': '2.1.222', 'engines': {'node': '>=22.0.0'}},
        ).encode('utf-8')
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = payload
        with mock.patch.object(avu.urllib.request, 'urlopen', return_value=response):
            self.assertEqual(
                avu._fetch_latest_package('@anthropic-ai/claude-code'),
                {'version': '2.1.222', 'node': '>=22.0.0'},
            )


class UpgradeRunTests(unittest.TestCase):
    """Running the planned command (the synchronous one-shot)."""

    ENABLED = {'KATO_AGENT_BACKEND': 'claude', 'KATO_ALLOW_CLI_UPGRADE': 'true'}

    def setUp(self):
        # ``upgrade_agent_cli`` plans before it runs, and planning consults the
        # registry for the Node-engine gate — stub it so nothing reaches out.
        registry = _registry()
        registry.start()
        self.addCleanup(registry.stop)
        self.addCleanup(avu.reset_latest_version_cache)

    def test_blocked_reason_explains_why_no_button(self):
        # Outdated but can't upgrade in-app (Docker) → reason is surfaced.
        info = _info({**self.ENABLED, 'KATO_CLAUDE_DOCKER': 'true'}, '2.1.142')
        self.assertFalse(info['can_upgrade'])
        self.assertIn('Docker', info['upgrade_blocked_reason'])
        # Hard-disabled also explains itself.
        disabled = _info(
            {'KATO_AGENT_BACKEND': 'claude', 'KATO_ALLOW_CLI_UPGRADE': 'false'},
            '2.1.142')
        self.assertIn('disabled', disabled['upgrade_blocked_reason'])

    def test_blocked_reason_empty_when_upgradable_or_current(self):
        self.assertEqual(_info(self.ENABLED, '2.1.142')['upgrade_blocked_reason'], '')
        self.assertEqual(_info(self.ENABLED, '2.1.170')['upgrade_blocked_reason'], '')

    def test_runs_fixed_command_when_enabled(self):
        captured = {}

        def runner(cmd):
            captured['cmd'] = cmd
            return 0, 'changed 1 package'

        with mock.patch.object(avu.shutil, 'which', return_value='/usr/bin/npm'), \
                _npm_managed():
            result = avu.upgrade_agent_cli(env=self.ENABLED, runner=runner)
        self.assertTrue(result['ok'])
        self.assertEqual(
            captured['cmd'][:4],
            ['/usr/bin/npm', 'install', '-g', '@anthropic-ai/claude-code@latest'],
        )

    def test_refused_when_hard_disabled(self):
        result = avu.upgrade_agent_cli(
            env={'KATO_AGENT_BACKEND': 'claude', 'KATO_ALLOW_CLI_UPGRADE': 'false'},
            runner=lambda c: (0, ''),
        )
        self.assertFalse(result['ok'])
        self.assertIn('disabled', result['message'])

    def test_reports_nonzero_exit(self):
        with mock.patch.object(avu.shutil, 'which', return_value='/usr/bin/npm'), \
                _npm_managed():
            result = avu.upgrade_agent_cli(env=self.ENABLED, runner=lambda c: (1, 'EACCES'))
        self.assertFalse(result['ok'])


class UpgradePlanTests(unittest.TestCase):
    """Which upgrade command suits THIS host's install."""

    ENABLED = {'KATO_AGENT_BACKEND': 'claude', 'KATO_ALLOW_CLI_UPGRADE': 'true'}

    def setUp(self):
        # No registry, no engine gate — the plan's shape is what's under test.
        registry = _registry()
        registry.start()
        self.addCleanup(registry.stop)
        self.addCleanup(avu.reset_latest_version_cache)

    def test_npm_install_upgrades_via_npm(self):
        with mock.patch.object(avu.shutil, 'which', return_value='/usr/bin/npm'), \
                _npm_managed():
            plan = avu.upgrade_plan(self.ENABLED)
        self.assertTrue(plan['allowed'])
        self.assertEqual(plan['manager'], 'npm')
        self.assertEqual(plan['command'], 'npm install -g @anthropic-ai/claude-code@latest')

    def test_native_install_defers_to_the_cli_self_updater(self):
        # Running `npm install -g` against a natively-installed CLI would leave
        # TWO copies on the machine and a PATH coin-flip over which one runs.
        with mock.patch.object(avu.shutil, 'which', return_value='/usr/local/bin/claude'), \
                _npm_managed(_NATIVE_REAL_PATH):
            plan = avu.upgrade_plan(self.ENABLED)
        self.assertTrue(plan['allowed'])
        self.assertEqual(plan['manager'], 'cli')
        self.assertEqual(plan['command'], 'claude update')
        self.assertEqual(plan['argv'], ['/usr/local/bin/claude', 'update'])

    def test_codex_upgrades_via_its_own_npm_package(self):
        env = {'KATO_AGENT_BACKEND': 'codex', 'KATO_ALLOW_CLI_UPGRADE': 'true'}
        with mock.patch.object(avu.shutil, 'which', return_value='/usr/bin/npm'), \
                _npm_managed('/usr/local/lib/node_modules/@openai/codex/bin/codex.js'):
            plan = avu.upgrade_plan(env)
        self.assertEqual(plan['manager'], 'npm')
        self.assertEqual(plan['command'], 'npm install -g @openai/codex@latest')

    def test_no_npm_and_no_self_updater_is_blocked_with_a_reason(self):
        # codex has no `codex update`, so a codex CLI installed outside npm on a
        # host without npm has no in-app path — say so instead of dead-ending
        # on "npm not found".
        env = {'KATO_AGENT_BACKEND': 'codex', 'KATO_ALLOW_CLI_UPGRADE': 'true'}
        with mock.patch.object(avu.shutil, 'which', return_value=None):
            plan = avu.upgrade_plan(env)
        self.assertFalse(plan['allowed'])
        self.assertIn('no supported upgrade path', plan['reason'])

    def test_missing_binary_still_plans_an_npm_install(self):
        # "claude not found on PATH" is exactly when the operator most needs the
        # install button — there is nothing to detect, so npm is the answer.
        def which(name):
            return '/usr/bin/npm' if name == 'npm' else None

        with mock.patch.object(avu.shutil, 'which', side_effect=which):
            plan = avu.upgrade_plan(self.ENABLED)
        self.assertTrue(plan['allowed'])
        self.assertEqual(plan['manager'], 'npm')

    def test_too_old_a_node_blocks_the_npm_plan_with_the_fix(self):
        # Real case on the reporter's host: claude-code 2.1.222 declares
        # engines.node ">=22.0.0" while the machine runs Node v20 — npm aborts
        # with EBADENGINE, so offering the button guarantees a failure.
        with _registry('2.1.222', '>=22.0.0'), \
                mock.patch.object(avu.shutil, 'which', return_value='/usr/bin/npm'), \
                mock.patch.object(avu, '_host_node_major', return_value=20), \
                _npm_managed():
            plan = avu.upgrade_plan(self.ENABLED)
        self.assertFalse(plan['allowed'])
        self.assertIn('Node >=22', plan['reason'])
        self.assertIn('Node 20', plan['reason'])
        self.assertEqual(plan['argv'], [])

    def test_a_new_enough_node_does_not_block(self):
        with _registry('2.1.222', '>=22.0.0'), \
                mock.patch.object(avu.shutil, 'which', return_value='/usr/bin/npm'), \
                mock.patch.object(avu, '_host_node_major', return_value=22), \
                _npm_managed():
            plan = avu.upgrade_plan(self.ENABLED)
        self.assertTrue(plan['allowed'])
        self.assertEqual(plan['manager'], 'npm')

    def test_an_unknown_node_requirement_never_blocks(self):
        # Fail OPEN: a wrong "your Node is too old" hides a working upgrade,
        # which is worse than letting npm speak for itself.
        for engines, host in (('^20 || ^22', 18), ('*', 18), ('', 18), ('>=x', 18)):
            with self.subTest(engines=engines), \
                    _registry('2.1.222', engines), \
                    mock.patch.object(avu.shutil, 'which', return_value='/usr/bin/npm'), \
                    mock.patch.object(avu, '_host_node_major', return_value=host), \
                    _npm_managed():
                self.assertTrue(avu.upgrade_plan(self.ENABLED)['allowed'])

    def test_an_undetectable_host_node_never_blocks(self):
        with _registry('2.1.222', '>=22.0.0'), \
                mock.patch.object(avu.shutil, 'which', return_value='/usr/bin/npm'), \
                mock.patch.object(avu, '_host_node_major', return_value=None), \
                _npm_managed():
            self.assertTrue(avu.upgrade_plan(self.ENABLED)['allowed'])

    def test_required_node_major_parsing(self):
        self.assertEqual(avu._required_node_major('>=22.0.0'), 22)
        self.assertEqual(avu._required_node_major('>= 20'), 20)
        self.assertIsNone(avu._required_node_major('^20 || ^22'))
        self.assertIsNone(avu._required_node_major('>=20 <23'))
        self.assertIsNone(avu._required_node_major(''))
        self.assertIsNone(avu._required_node_major(None))

    def test_host_node_major_reads_the_node_version(self):
        with mock.patch.object(avu.shutil, 'which', return_value='/usr/bin/node'):
            self.assertEqual(avu._host_node_major(runner=lambda p: 'v20.19.5'), 20)
            self.assertIsNone(avu._host_node_major(runner=lambda p: 'nonsense'))
        with mock.patch.object(avu.shutil, 'which', return_value=None):
            self.assertIsNone(avu._host_node_major())

    def test_blocked_plan_carries_the_gate_reason(self):
        plan = avu.upgrade_plan(
            {'KATO_AGENT_BACKEND': 'claude', 'KATO_ALLOW_CLI_UPGRADE': 'false'})
        self.assertFalse(plan['allowed'])
        self.assertIn('disabled', plan['reason'])
        self.assertEqual(plan['argv'], [])


def _settings_env(env):
    """Pretend ``env`` is what the settings store resolves to right now."""
    return mock.patch(
        'kato_core_lib.helpers.kato_settings_store_utils.effective_config_env',
        return_value=dict(env),
    )


class DefaultConfigEnvTests(unittest.TestCase):
    """The env default must be the SETTINGS-aware one, not ``os.environ``.

    Every key this module reads is an operator setting stored in
    ``~/.kato/settings.json``, and saving one does not mutate the running
    process env. Defaulting to ``os.environ`` made the probe disagree with
    ``/api/config-status``: an operator who pointed ``KATO_CLAUDE_BINARY`` at
    an absolute path cleared the setup gate while the banner kept reporting
    "not found on PATH" until a kato restart.
    """

    SETTINGS = {'KATO_AGENT_BACKEND': 'codex', 'KATO_CODEX_BINARY': 'mycodex'}

    def test_version_probe_reads_a_binary_set_only_in_settings(self):
        with _settings_env(self.SETTINGS), \
                mock.patch.dict(avu.os.environ, {}, clear=True), \
                mock.patch.object(avu.shutil, 'which', return_value='/x/mycodex') as which, \
                _registry():
            info = avu.agent_version_info(runner=lambda path: 'codex 1.2.3')

        which.assert_any_call('mycodex')
        self.assertEqual(info['binary'], 'mycodex')
        self.assertTrue(info['found'])

    def test_upgrade_plan_reads_the_same_settings_as_the_probe(self):
        """Or the button appears and then upgrades a binary kato can't see."""
        settings = {'KATO_AGENT_BACKEND': 'claude',
                    'KATO_CLAUDE_BINARY': '/opt/claude',
                    'KATO_ALLOW_CLI_UPGRADE': 'true'}
        with _settings_env(settings), \
                mock.patch.dict(avu.os.environ, {}, clear=True), \
                mock.patch.object(avu.shutil, 'which', return_value='/opt/claude'), \
                _npm_managed(_NATIVE_REAL_PATH), _registry('9.9.9'):
            plan = avu.upgrade_plan()

        self.assertTrue(plan['allowed'])
        self.assertIn('/opt/claude', plan['command'])

    def test_installed_version_reads_settings_too(self):
        with _settings_env(self.SETTINGS), \
                mock.patch.dict(avu.os.environ, {}, clear=True), \
                mock.patch.object(avu.shutil, 'which', return_value='/x/mycodex'):
            version = avu.installed_version(runner=lambda path: 'codex 4.5.6')

        self.assertEqual(version, '4.5.6')

    def test_explicit_env_still_wins_over_the_settings_store(self):
        with _settings_env(self.SETTINGS), \
                mock.patch.object(avu.shutil, 'which', return_value='/x/other') as which, \
                _registry():
            avu.agent_version_info(
                env={'KATO_AGENT_BACKEND': 'codex', 'KATO_CODEX_BINARY': 'other'},
                runner=lambda path: 'codex 1.2.3',
            )

        which.assert_any_call('other')

    def test_falls_back_to_process_env_when_the_store_is_unreadable(self):
        """A version probe degrades; it never raises."""
        broken = mock.patch(
            'kato_core_lib.helpers.kato_settings_store_utils.effective_config_env',
            side_effect=OSError('settings.json unreadable'),
        )
        with broken, mock.patch.dict(
            avu.os.environ,
            {'KATO_AGENT_BACKEND': 'codex', 'KATO_CODEX_BINARY': 'fromenv'},
            clear=True,
        ), mock.patch.object(avu.shutil, 'which', return_value='/x/fromenv') as which, \
                _registry():
            info = avu.agent_version_info(runner=lambda path: 'codex 1.2.3')

        which.assert_any_call('fromenv')
        self.assertTrue(info['found'])


if __name__ == '__main__':
    unittest.main()
