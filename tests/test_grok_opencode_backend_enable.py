"""Grok and OpenCode get their chat tabs exactly the way Codex does.

The chain pinned here mirrors ``test_codex_backend_enable.py`` for each new
CLI: a settings-schema field → the config block → a wired session manager →
the backends the chat pane draws tabs from. Each is ON by default (the YAML
default is "true"), and its Enable switch must draw ON when nothing is stored.
"""

from __future__ import annotations

import logging
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kato_core_lib.helpers.kato_settings_schema_utils import (
    DEFAULT_ON_BOOL_KEYS,
    all_settings_keys,
    schema_for_api,
    validate_settings_values,
)

_CLIS = (('grok', 'GROK'), ('opencode', 'OPENCODE'))
_YAML = (
    Path(__file__).resolve().parent.parent / 'kato_core_lib' / 'config' / 'kato_core_lib.yaml'
).read_text(encoding='utf-8')


def _yaml_block(name: str) -> str:
    following = re.split(r'\n  [a-z_]+:\n', _YAML.split(f'\n  {name}:\n', 1)[1], maxsplit=1)
    return following[0]


class SettingsTests(unittest.TestCase):
    def test_each_cli_has_its_own_section_with_an_enable_switch(self) -> None:
        for name, prefix in _CLIS:
            with self.subTest(cli=name):
                section = next(s for s in schema_for_api() if s['id'] == f'{name}_agent')
                enable = next(f for f in section['fields'] if f['key'] == f'KATO_{prefix}_ENABLED')
                self.assertEqual(enable['type'], 'bool')
                self.assertIn(f'KATO_{prefix}_ENABLED', DEFAULT_ON_BOOL_KEYS)
                self.assertEqual(validate_settings_values({f'KATO_{prefix}_ENABLED': 'true'}), [])

    def test_every_yaml_knob_is_exposed_and_no_bypass_switch_is_offered(self) -> None:
        for name, prefix in _CLIS:
            with self.subTest(cli=name):
                in_yaml = set(re.findall(rf'KATO_{prefix}_[A-Z_]+', _yaml_block(name)))
                self.assertTrue(in_yaml)
                self.assertEqual(in_yaml - all_settings_keys(), set())
                # No Docker sandbox image exists for either: the client
                # refuses bypass, so the settings offer no switch for it.
                self.assertNotIn(f'KATO_{prefix}_BYPASS_PERMISSIONS', all_settings_keys())

    def test_the_codex_enable_switch_draws_on_too(self) -> None:
        self.assertIn('KATO_CODEX_ENABLED', DEFAULT_ON_BOOL_KEYS)


class WiringTests(unittest.TestCase):
    def _backends(self, *, grok: bool, opencode: bool, agent_backend: str = 'claude'):
        from omegaconf import OmegaConf
        from kato_core_lib.kato_core_lib import KatoCoreLib
        cfg = OmegaConf.create({
            'agent_backend': agent_backend,
            'claude': {'binary': 'claude'},
            'codex': {'enabled': False, 'binary': 'codex'},
            'grok': {'enabled': grok, 'binary': 'grok'},
            'opencode': {'enabled': opencode, 'binary': 'opencode'},
        })
        instance = KatoCoreLib.__new__(KatoCoreLib)
        instance.logger = logging.getLogger('test-grok-opencode-wiring')
        with tempfile.TemporaryDirectory(prefix='per-turn-wiring-') as tmp:
            with patch('kato_core_lib.kato_core_lib.kato_session_state_dir', return_value=tmp):
                router = instance._build_session_manager(cfg, agent_backend)
            self.assertIsNotNone(router)
            return sorted(router.available_backends()), router

    def test_off_yields_only_claude(self) -> None:
        self.assertEqual(self._backends(grok=False, opencode=False)[0], ['claude'])

    def test_on_wires_a_real_manager_for_each(self) -> None:
        backends, router = self._backends(grok=True, opencode=True)
        self.assertEqual(backends, ['claude', 'grok', 'opencode'])
        from grok_core_lib.grok_core_lib.session.manager import GrokSessionManager
        from opencode_core_lib.opencode_core_lib.session.manager import OpenCodeSessionManager
        managers = router._managers
        self.assertIsInstance(managers['grok'], GrokSessionManager)
        self.assertIsInstance(managers['opencode'], OpenCodeSessionManager)

    def test_a_cli_with_no_config_block_is_not_wired(self) -> None:
        from omegaconf import OmegaConf
        from kato_core_lib.kato_core_lib import KatoCoreLib
        cfg = OmegaConf.create({'agent_backend': 'claude', 'claude': {'binary': 'claude'}})
        instance = KatoCoreLib.__new__(KatoCoreLib)
        instance.logger = logging.getLogger('test-grok-opencode-wiring')
        with tempfile.TemporaryDirectory(prefix='per-turn-wiring-') as tmp:
            with patch('kato_core_lib.kato_core_lib.kato_session_state_dir', return_value=tmp):
                router = instance._build_session_manager(cfg, 'claude')
        self.assertEqual(sorted(router.available_backends()), ['claude'])


class ReadinessAndRunnerTests(unittest.TestCase):
    def test_every_chat_cli_gets_a_tab_and_a_label(self) -> None:
        from kato_core_lib.helpers.agent_backend_readiness import CHAT_BACKENDS, backend_label
        self.assertEqual(CHAT_BACKENDS, ('claude', 'codex', 'grok', 'opencode'))
        self.assertEqual(backend_label('grok'), 'Grok')
        self.assertEqual(backend_label('opencode'), 'OpenCode')

    def test_the_readiness_probe_builds_each_cli_s_own_client(self) -> None:
        from kato_core_lib.helpers import agent_backend_readiness as readiness
        from grok_core_lib.grok_core_lib.cli_client import GrokCliClient
        from opencode_core_lib.opencode_core_lib.cli_client import OpenCodeCliClient
        self.assertIsInstance(readiness._build_probe_client('grok', ''), GrokCliClient)
        self.assertIsInstance(readiness._build_probe_client('opencode', '/opt/oc'), OpenCodeCliClient)
        self.assertEqual(readiness._build_probe_client('opencode', '/opt/oc')._binary, '/opt/oc')
        self.assertIsNone(readiness._build_probe_client('openhands', ''))

    def test_each_tab_spawns_its_own_binary(self) -> None:
        from omegaconf import OmegaConf
        from kato_core_lib.data_layers.service.planning_session_runner import PlanningSessionRunner
        cfg = OmegaConf.create({
            'claude': {'binary': 'claude'}, 'grok': {'binary': '/opt/grok'},
            'opencode': {'binary': '/opt/opencode'},
        })
        runner = PlanningSessionRunner.from_config(cfg, 'claude', session_manager=object())
        self.assertEqual(runner._defaults_by_backend['grok'].binary, '/opt/grok')
        self.assertEqual(runner._defaults_by_backend['opencode'].binary, '/opt/opencode')

    def test_the_version_helper_knows_each_cli(self) -> None:
        from kato_core_lib.helpers.agent_version_utils import binary_for_backend, upgrade_allowed
        self.assertEqual(binary_for_backend('grok', {}), 'grok')
        self.assertEqual(binary_for_backend('opencode', {'KATO_OPENCODE_BINARY': '/x/oc'}), '/x/oc')
        allowed, reason = upgrade_allowed({}, 'grok')
        self.assertFalse(allowed)
        self.assertIn('npm', reason)


if __name__ == '__main__':
    unittest.main()
