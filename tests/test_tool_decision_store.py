"""Tests for the backend-owned remembered tool-permission decision store.

Remembered decisions ("Allow always" / "Deny always") must be owned by
the server, not the browser, so the client is never the one deciding
what gets approved — see the module docstring in
kato_core_lib/helpers/tool_decision_store.py. The path is
env-overridable so the test never touches the real ``~/.kato``.
"""
from __future__ import annotations

import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from kato_core_lib.helpers.tool_decision_store import (
    forget_tool_decision,
    list_tool_decisions,
    read_tool_decisions,
    recall_tool_decision,
    remember_tool_decision,
)


class ToolDecisionStoreTests(unittest.TestCase):

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self._path = str(Path(self._td.name) / 'tool_decisions.json')
        patcher = unittest.mock.patch.dict(
            os.environ, {'KATO_TOOL_DECISIONS_PATH': self._path},
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_recall_is_none_when_nothing_remembered(self) -> None:
        self.assertIsNone(recall_tool_decision('Bash', 'mvn'))

    def test_remember_allow_then_recall(self) -> None:
        remember_tool_decision('Bash', 'mvn', True)
        self.assertTrue(recall_tool_decision('Bash', 'mvn'))
        self.assertTrue(Path(self._path).is_file())

    def test_remember_deny_then_recall(self) -> None:
        remember_tool_decision('Bash', 'rm', False)
        self.assertFalse(recall_tool_decision('Bash', 'rm'))

    def test_different_signatures_are_independent(self) -> None:
        remember_tool_decision('Bash', 'mvn', True)
        remember_tool_decision('Bash', 'rm', False)
        self.assertTrue(recall_tool_decision('Bash', 'mvn'))
        self.assertFalse(recall_tool_decision('Bash', 'rm'))
        self.assertIsNone(recall_tool_decision('Bash', 'docker'))

    def test_different_tools_are_independent(self) -> None:
        remember_tool_decision('Bash', 'mvn', True)
        remember_tool_decision('Docker', 'mvn', False)
        self.assertTrue(recall_tool_decision('Bash', 'mvn'))
        self.assertFalse(recall_tool_decision('Docker', 'mvn'))

    def test_tool_level_decision_uses_empty_signature(self) -> None:
        # Non-command-keyed tools (Edit, Write, ...) remember at the
        # tool level — command_signature is ''.
        remember_tool_decision('Edit', '', True)
        self.assertTrue(recall_tool_decision('Edit', ''))
        self.assertIsNone(recall_tool_decision('Write', ''))

    def test_remember_overwrites_previous_decision(self) -> None:
        remember_tool_decision('Bash', 'mvn', True)
        remember_tool_decision('Bash', 'mvn', False)
        self.assertFalse(recall_tool_decision('Bash', 'mvn'))

    def test_forget_removes_entry(self) -> None:
        remember_tool_decision('Bash', 'mvn', True)
        forget_tool_decision('Bash', 'mvn')
        self.assertIsNone(recall_tool_decision('Bash', 'mvn'))

    def test_forget_unknown_entry_is_noop(self) -> None:
        forget_tool_decision('Bash', 'nope')
        self.assertFalse(Path(self._path).is_file())

    def test_blank_tool_name_is_noop(self) -> None:
        remember_tool_decision('', 'mvn', True)
        self.assertFalse(Path(self._path).is_file())

    def test_unreadable_file_returns_empty(self) -> None:
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        Path(self._path).write_text('not json {{{', encoding='utf-8')
        self.assertEqual(read_tool_decisions(), {})
        self.assertIsNone(recall_tool_decision('Bash', 'mvn'))

    def test_non_dict_payload_returns_empty(self) -> None:
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        Path(self._path).write_text('["a", "b"]', encoding='utf-8')
        self.assertEqual(read_tool_decisions(), {})

    def test_invalid_decision_values_are_dropped(self) -> None:
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        Path(self._path).write_text(
            '{"Bash mvn": "allow", "Bash rm": "maybe"}', encoding='utf-8',
        )
        decisions = read_tool_decisions()
        self.assertIn('Bash mvn', decisions)
        self.assertNotIn('Bash rm', decisions)

    def test_list_tool_decisions_shape_and_order(self) -> None:
        remember_tool_decision('Bash', 'rm', False)
        remember_tool_decision('Bash', 'mvn', True)
        remember_tool_decision('Edit', '', True)
        entries = list_tool_decisions()
        self.assertEqual(
            entries,
            [
                {'tool_name': 'Bash', 'command_signature': 'mvn', 'allow': True},
                {'tool_name': 'Bash', 'command_signature': 'rm', 'allow': False},
                {'tool_name': 'Edit', 'command_signature': '', 'allow': True},
            ],
        )


if __name__ == '__main__':
    unittest.main()
