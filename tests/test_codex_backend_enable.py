"""Turning Codex on from the Settings UI must actually wire a Codex tab.

The chat pane derives its agent tabs from what is WIRED
(``/api/agent-backends`` → ``available_backends``), and the Codex config
block is gated OFF by default so a host with no ``codex`` binary doesn't
grow a tab whose first message fails.

The gate was reachable only by hand-setting an env var — the flag existed
in the YAML and NOWHERE in the settings schema, so the UI offered no way to
turn Codex on and the operator saw a Claude tab and nothing else. These
tests pin the whole chain: schema field → config value → wired manager →
the backends the endpoint reports.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from kato_core_lib.helpers.kato_settings_schema_utils import (
    all_settings_keys,
    schema_for_api,
    validate_settings_values,
)


class CodexSettingsSchemaTests(unittest.TestCase):
    """The flag is reachable from the settings UI."""

    def _codex_section(self):
        sections = [s for s in schema_for_api() if s['id'] == 'codex_agent']
        self.assertEqual(len(sections), 1, 'codex_agent section missing')
        return sections[0]

    def test_codex_section_exists_with_the_enable_flag(self) -> None:
        keys = [f['key'] for f in self._codex_section()['fields']]
        self.assertIn('KATO_CODEX_ENABLED', keys)

    def test_the_enable_flag_is_a_checkbox(self) -> None:
        field = next(
            f for f in self._codex_section()['fields']
            if f['key'] == 'KATO_CODEX_ENABLED'
        )
        self.assertEqual(field['type'], 'bool')

    def test_the_flag_is_writable_through_the_settings_endpoint(self) -> None:
        # The server whitelists writes to the schema — a key absent from it
        # is silently dropped, which is exactly how this was unreachable.
        self.assertIn('KATO_CODEX_ENABLED', all_settings_keys())
        self.assertEqual(validate_settings_values({'KATO_CODEX_ENABLED': 'true'}), [])

    def test_every_codex_yaml_knob_is_exposed(self) -> None:
        """No codex setting is configurable ONLY by hand-editing env."""
        import re
        from pathlib import Path
        yaml_text = (
            Path(__file__).resolve().parent.parent
            / 'kato_core_lib' / 'config' / 'kato_core_lib.yaml'
        ).read_text(encoding='utf-8')
        block = yaml_text.split('\n  codex:\n', 1)[1].split('\n  task_scan:', 1)[0]
        in_yaml = set(re.findall(r'KATO_CODEX_[A-Z_]+', block))
        # Schema-WIDE, not just this section: the bypass switch deliberately
        # sits beside the Claude one in Sandbox, under the same danger text.
        self.assertEqual(
            in_yaml - all_settings_keys(), set(),
            'codex knobs in the YAML with no settings-schema field',
        )


class CodexWiringTests(unittest.TestCase):
    """The flag reaches the router, not just the form."""

    def _backends(self, enabled, agent_backend='claude'):
        """Real ``_build_session_manager`` against a real config object.

        Only the state directory is redirected (a temp dir) — the managers
        themselves are constructed for real, which is the point: a manager
        that cannot be built is a tab that cannot chat.
        """
        import logging
        import tempfile
        from omegaconf import OmegaConf
        from kato_core_lib.kato_core_lib import KatoCoreLib
        cfg = OmegaConf.create({
            'agent_backend': agent_backend,
            'claude': {'binary': 'claude'},
            'codex': {'enabled': enabled, 'binary': 'codex'},
        })
        instance = KatoCoreLib.__new__(KatoCoreLib)
        instance.logger = logging.getLogger('test-codex-wiring')
        with tempfile.TemporaryDirectory(prefix='kato-codex-wiring-') as tmp:
            with patch(
                'kato_core_lib.kato_core_lib.kato_session_state_dir',
                return_value=tmp,
            ):
                router = instance._build_session_manager(cfg, agent_backend)
            self.assertIsNotNone(router, 'no session manager was built')
            return sorted(router.available_backends())

    def test_codex_off_yields_no_codex_backend(self) -> None:
        self.assertEqual(self._backends(False), ['claude'])

    def test_codex_on_yields_a_codex_backend(self) -> None:
        # The whole point of the flag: the chat pane's tabs come from here.
        self.assertEqual(self._backends(True), ['claude', 'codex'])

    def test_running_codex_as_the_backend_wires_it_regardless(self) -> None:
        # Configured FOR codex but with the flag off is a contradiction —
        # the backend it was told to run wins.
        self.assertIn('codex', self._backends(False, agent_backend='codex'))


if __name__ == '__main__':
    unittest.main()
