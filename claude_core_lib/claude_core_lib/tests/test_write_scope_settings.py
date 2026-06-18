"""Tests for the out-of-workspace write-approval settings."""
from __future__ import annotations

import json
import unittest

from claude_core_lib.claude_core_lib.helpers.write_scope_settings import (
    out_of_workspace_write_ask_rules,
    out_of_workspace_write_settings,
    out_of_workspace_write_settings_json,
)


class OutOfWorkspaceWriteSettingsTests(unittest.TestCase):
    def test_covers_every_write_tool_for_tmp(self) -> None:
        rules = out_of_workspace_write_ask_rules()
        for tool in ('Write', 'Edit', 'MultiEdit', 'NotebookEdit'):
            self.assertIn(f'{tool}(/tmp/**)', rules)

    def test_covers_dangerous_system_roots(self) -> None:
        rules = out_of_workspace_write_ask_rules()
        for root in ('/etc', '/usr', '/var', '/bin', '/root'):
            self.assertIn(f'Write({root}/**)', rules)

    def test_covers_mounted_volume_roots(self) -> None:
        # Mounted/external/network drives are classic exfil targets and are
        # never the task workspace, so writes there must be approved too.
        rules = out_of_workspace_write_ask_rules()
        for root in ('/Volumes', '/Network', '/mnt', '/media', '/srv'):
            for tool in ('Write', 'Edit', 'MultiEdit', 'NotebookEdit'):
                self.assertIn(f'{tool}({root}/**)', rules)

    def test_never_targets_home_or_workspace(self) -> None:
        # The task workspace lives under ~/.kato/workspaces — a home rule would
        # prompt on every in-workspace edit and defeat acceptEdits.
        for rule in out_of_workspace_write_ask_rules():
            self.assertNotIn('~', rule)
            self.assertNotIn('.kato', rule)

    def test_settings_shape_is_permissions_ask(self) -> None:
        settings = out_of_workspace_write_settings()
        self.assertEqual(list(settings.keys()), ['permissions'])
        self.assertEqual(list(settings['permissions'].keys()), ['ask'])
        self.assertEqual(
            settings['permissions']['ask'], out_of_workspace_write_ask_rules())

    def test_json_is_valid_and_compact(self) -> None:
        raw = out_of_workspace_write_settings_json()
        self.assertNotIn(', ', raw)  # compact separators
        self.assertEqual(json.loads(raw), out_of_workspace_write_settings())


if __name__ == '__main__':
    unittest.main()
