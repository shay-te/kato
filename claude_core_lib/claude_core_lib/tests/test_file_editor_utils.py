"""Unit tests for the one-file Claude editor.

The subprocess is mocked. What is locked here is the BOX the run is put in —
which tools exist and which single file they reach — because that, not the
prompt, is what bounds what a run can do.
"""

from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from claude_core_lib.claude_core_lib.helpers.file_editor_utils import (
    EDITOR_TOOLS,
    file_editor_command,
    make_file_editor,
)
from claude_core_lib.claude_core_lib.helpers.one_shot_utils import OneShotError

_RUN = 'agent_core_lib.agent_core_lib.helpers.one_shot.subprocess.run'


class _CompletedProcess:
    def __init__(self, returncode: int = 0, stdout: str = '', stderr: str = '') -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FileEditorCommandTests(unittest.TestCase):
    def test_only_read_search_and_edit_exist_for_the_run(self) -> None:
        command = file_editor_command('/state/lessons.md')
        self.assertEqual(command[command.index('--tools') + 1], 'Read,Grep,Edit')
        # No shell, no network, no sub-agents, and no ``Write``: a run cannot
        # replace the document wholesale or create a file beside it.
        for forbidden in ('Bash', 'Write', 'WebFetch', 'Agent', 'Task'):
            self.assertNotIn(forbidden, EDITOR_TOOLS)

    def test_the_tools_are_granted_on_the_one_file_in_absolute_form(self) -> None:
        command = file_editor_command('/state/lessons.md')
        start = command.index('--allowedTools') + 1
        self.assertEqual(
            command[start:start + 3],
            ['Read(//state/lessons.md)', 'Edit(//state/lessons.md)', 'Grep'],
        )

    def test_no_mcp_servers_and_no_saved_session(self) -> None:
        command = file_editor_command('/state/lessons.md')
        self.assertIn('--strict-mcp-config', command)
        self.assertNotIn('--mcp-config', command)
        self.assertIn('--no-session-persistence', command)

    def test_model_is_forwarded_only_when_set(self) -> None:
        self.assertNotIn('--model', file_editor_command('/state/lessons.md'))
        command = file_editor_command('/state/lessons.md', binary='cc', model='opus')
        self.assertEqual(command[0], 'cc')
        self.assertEqual(command[command.index('--model') + 1], 'opus')


class MakeFileEditorTests(unittest.TestCase):
    def test_runs_the_boxed_command_with_the_prompt_on_stdin(self) -> None:
        editor = make_file_editor(
            '/state/lessons.md', cwd='/scratch', timeout_seconds=42,
        )
        with patch(_RUN, return_value=_CompletedProcess(0, 'DONE')) as run:
            self.assertEqual(editor('file these'), 'DONE')
        argv = run.call_args.args[0]
        self.assertIn('Edit(//state/lessons.md)', argv)
        self.assertEqual(run.call_args.kwargs['input'], 'file these')
        self.assertEqual(run.call_args.kwargs['cwd'], '/scratch')
        self.assertEqual(run.call_args.kwargs['timeout'], 42)

    def test_a_failed_run_raises_the_one_shot_error(self) -> None:
        editor = make_file_editor('/state/lessons.md')
        with patch(_RUN, return_value=_CompletedProcess(1, '', 'boom')):
            with self.assertRaises(OneShotError):
                editor('file these')

    def test_a_timeout_raises_the_one_shot_error(self) -> None:
        editor = make_file_editor('/state/lessons.md')
        with patch(_RUN, side_effect=subprocess.TimeoutExpired('claude', 1)):
            with self.assertRaises(OneShotError):
                editor('file these')


if __name__ == '__main__':
    unittest.main()
