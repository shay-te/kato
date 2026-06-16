import unittest
from unittest import mock

from kato_core_lib.helpers import agent_version_utils as avu


def _info(env, version_output='2.1.142 (Claude Code)', on_path=True):
    which = mock.patch.object(
        avu.shutil, 'which',
        return_value=('/usr/local/bin/agent' if on_path else None),
    )
    with which:
        return avu.agent_version_info(env=env, runner=lambda path: version_output)


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
        which.assert_called_with('mycodex')


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

    def test_gating(self):
        self.assertFalse(avu.upgrade_allowed({'KATO_AGENT_BACKEND': 'claude'})[0])
        self.assertTrue(avu.upgrade_allowed(self.ENABLED)[0])
        self.assertFalse(avu.upgrade_allowed(
            {'KATO_AGENT_BACKEND': 'codex', 'KATO_ALLOW_CLI_UPGRADE': 'true'})[0])
        self.assertFalse(avu.upgrade_allowed(
            {**self.ENABLED, 'KATO_CLAUDE_DOCKER': 'true'})[0])

    def test_can_upgrade_flag_in_info(self):
        info = _info(self.ENABLED, '2.1.142')
        self.assertTrue(info['can_upgrade'])
        self.assertEqual(
            info['upgrade_command'], 'npm install -g @anthropic-ai/claude-code@latest',
        )

    def test_can_upgrade_false_when_up_to_date_or_disabled(self):
        self.assertFalse(_info(self.ENABLED, '2.1.170')['can_upgrade'])
        self.assertFalse(_info({'KATO_AGENT_BACKEND': 'claude'}, '2.1.142')['can_upgrade'])

    def test_runs_fixed_command_when_enabled(self):
        captured = {}

        def runner(cmd):
            captured['cmd'] = cmd
            return 0, 'changed 1 package'

        with mock.patch.object(avu.shutil, 'which', return_value='/usr/bin/npm'):
            result = avu.upgrade_agent_cli(env=self.ENABLED, runner=runner)
        self.assertTrue(result['ok'])
        self.assertEqual(
            captured['cmd'],
            ['/usr/bin/npm', 'install', '-g', '@anthropic-ai/claude-code@latest'],
        )

    def test_refused_when_disabled(self):
        result = avu.upgrade_agent_cli(
            env={'KATO_AGENT_BACKEND': 'claude'}, runner=lambda c: (0, ''),
        )
        self.assertFalse(result['ok'])
        self.assertIn('disabled', result['message'])

    def test_reports_npm_missing(self):
        with mock.patch.object(avu.shutil, 'which', return_value=None):
            result = avu.upgrade_agent_cli(env=self.ENABLED, runner=lambda c: (0, ''))
        self.assertFalse(result['ok'])
        self.assertIn('npm not found', result['message'])

    def test_reports_nonzero_exit(self):
        with mock.patch.object(avu.shutil, 'which', return_value='/usr/bin/npm'):
            result = avu.upgrade_agent_cli(env=self.ENABLED, runner=lambda c: (1, 'EACCES'))
        self.assertFalse(result['ok'])


if __name__ == '__main__':
    unittest.main()
