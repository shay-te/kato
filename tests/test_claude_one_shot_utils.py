"""Unit tests for ``claude_one_shot``.

The subprocess is mocked. Locks the contract:
  * Returns stdout on success.
  * Raises ``ClaudeOneShotError`` on non-zero exit.
  * Raises ``ClaudeOneShotError`` on timeout.
  * Raises ``ClaudeOneShotError`` when the binary is missing.
  * Model flag is forwarded only when set.
"""

from __future__ import annotations

import subprocess
import unittest
from unittest.mock import MagicMock, patch

from claude_core_lib.claude_core_lib.helpers.one_shot_utils import (
    ClaudeOneShotError,
    claude_one_shot,
    make_claude_one_shot,
)


class _CompletedProcess:
    def __init__(self, returncode: int = 0, stdout: str = '', stderr: str = '') -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class ClaudeOneShotTests(unittest.TestCase):
    def test_returns_stdout_on_success(self) -> None:
        with patch(
            'claude_core_lib.claude_core_lib.helpers.one_shot_utils.subprocess.run',
            return_value=_CompletedProcess(0, 'the response'),
        ):
            self.assertEqual(claude_one_shot('hello'), 'the response')

    def test_passes_prompt_via_stdin(self) -> None:
        with patch(
            'claude_core_lib.claude_core_lib.helpers.one_shot_utils.subprocess.run',
            return_value=_CompletedProcess(0, 'ok'),
        ) as mock_run:
            claude_one_shot('the prompt content')
        kwargs = mock_run.call_args.kwargs
        self.assertEqual(kwargs['input'], 'the prompt content')
        self.assertTrue(kwargs['text'])
        self.assertTrue(kwargs['capture_output'])

    def test_command_includes_p_flag(self) -> None:
        with patch(
            'claude_core_lib.claude_core_lib.helpers.one_shot_utils.subprocess.run',
            return_value=_CompletedProcess(0, ''),
        ) as mock_run:
            claude_one_shot('x')
        cmd = mock_run.call_args.args[0]
        self.assertEqual(cmd[0], 'claude')
        self.assertIn('-p', cmd)

    def test_model_flag_added_when_set(self) -> None:
        with patch(
            'claude_core_lib.claude_core_lib.helpers.one_shot_utils.subprocess.run',
            return_value=_CompletedProcess(0, ''),
        ) as mock_run:
            claude_one_shot('x', model='claude-haiku-4-5-20251001')
        cmd = mock_run.call_args.args[0]
        self.assertIn('--model', cmd)
        self.assertIn('claude-haiku-4-5-20251001', cmd)

    def test_model_flag_omitted_when_empty(self) -> None:
        with patch(
            'claude_core_lib.claude_core_lib.helpers.one_shot_utils.subprocess.run',
            return_value=_CompletedProcess(0, ''),
        ) as mock_run:
            claude_one_shot('x', model='')
        cmd = mock_run.call_args.args[0]
        self.assertNotIn('--model', cmd)

    def test_custom_binary_used(self) -> None:
        with patch(
            'claude_core_lib.claude_core_lib.helpers.one_shot_utils.subprocess.run',
            return_value=_CompletedProcess(0, ''),
        ) as mock_run:
            claude_one_shot('x', binary='/usr/local/bin/claude')
        cmd = mock_run.call_args.args[0]
        self.assertEqual(cmd[0], '/usr/local/bin/claude')

    def test_nonzero_exit_raises(self) -> None:
        with patch(
            'claude_core_lib.claude_core_lib.helpers.one_shot_utils.subprocess.run',
            return_value=_CompletedProcess(1, '', 'auth required'),
        ):
            with self.assertRaisesRegex(ClaudeOneShotError, 'auth required'):
                claude_one_shot('x')

    def test_timeout_raises(self) -> None:
        with patch(
            'claude_core_lib.claude_core_lib.helpers.one_shot_utils.subprocess.run',
            side_effect=subprocess.TimeoutExpired(cmd='claude', timeout=10),
        ):
            with self.assertRaisesRegex(ClaudeOneShotError, 'within'):
                claude_one_shot('x', timeout_seconds=10)

    def test_missing_binary_raises(self) -> None:
        with patch(
            'claude_core_lib.claude_core_lib.helpers.one_shot_utils.subprocess.run',
            side_effect=OSError('no such file'),
        ):
            with self.assertRaisesRegex(ClaudeOneShotError, 'failed to invoke'):
                claude_one_shot('x', binary='nonexistent')


class MakeClaudeOneShotTests(unittest.TestCase):
    def test_closure_forwards_config(self) -> None:
        with patch(
            'claude_core_lib.claude_core_lib.helpers.one_shot_utils.subprocess.run',
            return_value=_CompletedProcess(0, 'response'),
        ) as mock_run:
            fn = make_claude_one_shot(
                binary='claude-bin',
                model='claude-x',
                timeout_seconds=42,
            )
            self.assertEqual(fn('prompt-text'), 'response')
        cmd = mock_run.call_args.args[0]
        self.assertEqual(cmd[0], 'claude-bin')
        self.assertIn('claude-x', cmd)
        self.assertEqual(mock_run.call_args.kwargs['timeout'], 42)


if __name__ == '__main__':
    unittest.main()
