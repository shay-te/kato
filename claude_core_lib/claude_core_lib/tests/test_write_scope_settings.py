"""Tests for the out-of-workspace write-approval settings.

The contract: a write to ANY path — a sibling repo under the home tree, /tmp,
anywhere — reaches the permission path (unscoped ask rules). This closes the
hole where a sibling repo under ``/Users`` matched no enumerated root and
slipped through with no prompt. The host answers the ones inside the task
folder.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from claude_core_lib.claude_core_lib.helpers.write_scope_settings import (
    agent_memory_read_deny_rules,
    agent_state_dir_write_deny_rules,
    out_of_workspace_write_ask_rules,
    out_of_workspace_write_settings,
    out_of_workspace_write_settings_json,
    out_of_workspace_write_settings_path,
)

_WRITE_TOOLS = ('Write', 'Edit', 'MultiEdit', 'NotebookEdit')
# The incident: task workspace vs. an unrelated sibling repo under home.
_CWD = '/Users/dev/workspaces/UNA-2763/ob-love-bridge'


class OutOfWorkspaceWriteSettingsTests(unittest.TestCase):
    def test_ask_rules_are_unscoped_catch_all(self) -> None:
        # Bare tool names → every write invocation reaches the permission path.
        self.assertEqual(out_of_workspace_write_ask_rules(), list(_WRITE_TOOLS))

    def test_no_rule_tries_to_let_a_write_through(self) -> None:
        # The CLI checks ``ask`` first, so an allow rule could only mislead.
        # The host answers each write instead (``plain_write``).
        self.assertNotIn('allow', out_of_workspace_write_settings(_CWD)['permissions'])

    def test_no_workspace_still_asks_for_every_write(self) -> None:
        settings = out_of_workspace_write_settings('')
        self.assertEqual(settings['permissions']['ask'], list(_WRITE_TOOLS))

    def test_settings_shape_is_permissions_ask_deny(self) -> None:
        # ``deny`` joined ask because the ask rules cannot reach the
        # CLI's own state directory: it auto-accepts writes there, so the
        # agent's memory kept landing in the global agent folder and the
        # operator only learned about it from an after-the-fact warning.
        # Without a known workspaces root there is no task folder, so no
        # ``autoMemoryDirectory`` key — cleared here so an environment that
        # happens to export the root cannot change this test's answer.
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('AGENT_WORKSPACES_ROOT', None)
            settings = out_of_workspace_write_settings(_CWD)
        self.assertEqual(list(settings.keys()), ['permissions'])
        self.assertEqual(sorted(settings['permissions'].keys()), ['ask', 'deny'])
        self.assertEqual(
            settings['permissions']['ask'], out_of_workspace_write_ask_rules())
        self.assertEqual(
            settings['permissions']['deny'],
            agent_state_dir_write_deny_rules() + agent_memory_read_deny_rules())

    def test_json_is_valid_and_compact(self) -> None:
        raw = out_of_workspace_write_settings_json(_CWD)
        self.assertNotIn(', ', raw)  # compact separators
        self.assertEqual(json.loads(raw), out_of_workspace_write_settings(_CWD))


if __name__ == '__main__':
    unittest.main()


class SettingsByPathTests(unittest.TestCase):
    """The settings go to the CLI as a file path, not as an inline blob.

    Inline, they count towards the command line, and past Windows' limit the
    spawn dies before the agent starts:

        send failed: failed to launch claude CLI binary "claude":
        [WinError 206] The filename or extension is too long

    Reported after a Stop + fresh prompt on a 25-repo task: "the chat is
    completly broken for this task".
    """

    def test_the_path_is_short(self) -> None:
        path = out_of_workspace_write_settings_path(os.path.join(tempfile.mkdtemp(), 'client'))
        self.assertTrue(path)
        self.assertLess(len(path), 300)

    def test_the_file_holds_exactly_the_same_settings(self) -> None:
        # A shorter command line is worthless if it weakens the write scope.
        cwd = os.path.join(tempfile.mkdtemp(), 'client')

        path = out_of_workspace_write_settings_path(cwd)
        with open(path, encoding='utf-8') as handle:
            from_file = json.load(handle)

        self.assertEqual(from_file, json.loads(out_of_workspace_write_settings_json(cwd)))

    def test_the_file_is_written_OUTSIDE_the_workspace(self) -> None:
        """This file is what forces out-of-workspace writes to be approved.

        Inside the workspace the agent could edit it and widen its own
        permissions, which is the one place it must not live.
        """
        workspace = tempfile.mkdtemp()
        path = out_of_workspace_write_settings_path(workspace)

        self.assertFalse(
            Path(path).is_relative_to(Path(workspace)),
            f'settings landed inside the agent-writable workspace: {path}',
        )

    def test_settings_that_differ_do_not_share_a_file(self) -> None:
        a = out_of_workspace_write_settings_path('/tmp/ws', dedupe_reads=True)
        b = out_of_workspace_write_settings_path('/tmp/ws', dedupe_reads=False)
        self.assertNotEqual(a, b)

    def test_an_unwritable_location_returns_empty_for_the_caller_to_fall_back(self) -> None:
        # A too-long command line is still better than launching the agent
        # with NO write-scope settings, so the caller keeps the inline string
        # as its fallback and this must signal failure rather than raise.
        with patch(
            'claude_core_lib.claude_core_lib.helpers.write_scope_settings.'
            'atomic_write_json',
            side_effect=OSError('read-only file system'),
        ):
            self.assertEqual(
                out_of_workspace_write_settings_path('/tmp/ws'), '',
            )


class LessonsGateWiringTests(unittest.TestCase):
    """The gate ships with the settings, and only when there is a file."""

    def test_the_hook_is_included_when_a_lessons_file_is_configured(self) -> None:
        with patch.dict(os.environ, {'AGENT_LESSONS_PATH': '/ws/lessons.md'}):
            settings = out_of_workspace_write_settings('/ws', ('/ws',))
        hooks = settings['hooks']['PreToolUse']
        self.assertTrue(any('lessons_gate' in h['hooks'][0]['command'] for h in hooks))
        # ``*``, not a tool list: a new CLI capability must be gated the
        # moment it exists, not when someone remembers to add it.
        gate = next(h for h in hooks if 'lessons_gate' in h['hooks'][0]['command'])
        self.assertEqual(gate['matcher'], '*')

    def test_the_gate_is_told_what_each_read_returned(self) -> None:
        # ``PreToolUse`` sees a read being ASKED for; only ``PostToolUse``
        # carries the lines that came back. Without it the gate opened on the
        # first call that named the file — a failed read, or page one of five.
        with patch.dict(os.environ, {'AGENT_LESSONS_PATH': '/ws/lessons.md'}):
            settings = out_of_workspace_write_settings('/ws', ('/ws',))
        after = settings['hooks']['PostToolUse']
        self.assertEqual([h['matcher'] for h in after], ['Read'])
        self.assertIn('lessons_gate', after[0]['hooks'][0]['command'])
        # The same module on both events: one record, two feeds.
        before = next(
            h for h in settings['hooks']['PreToolUse']
            if 'lessons_gate' in h['hooks'][0]['command']
        )
        self.assertEqual(
            after[0]['hooks'][0]['command'], before['hooks'][0]['command'],
        )

    def test_no_lessons_file_means_no_hook(self) -> None:
        # ``read_lessons_file`` injects no directive for a missing or blank
        # file; a gate without that directive would deny every tool while
        # nothing on screen explained why.
        env = dict(os.environ)
        env.pop('AGENT_LESSONS_PATH', None)
        with patch.dict(os.environ, env, clear=True):
            settings = out_of_workspace_write_settings('/ws', ('/ws',))
        hooks = settings.get('hooks', {}).get('PreToolUse', [])
        self.assertFalse(any('lessons_gate' in h['hooks'][0]['command'] for h in hooks))
        self.assertNotIn('PostToolUse', settings.get('hooks', {}))

    def test_both_hooks_survive_together(self) -> None:
        # Two ``settings.update({'hooks': ...})`` calls would leave only the
        # last one's PreToolUse list — the read-dedupe hook would vanish the
        # moment a lessons file existed.
        with patch.dict(os.environ, {'AGENT_LESSONS_PATH': '/ws/lessons.md'}):
            settings = out_of_workspace_write_settings(
                '/ws', dedupe_reads=True,
            )
        commands = [
            h['hooks'][0]['command'] for h in settings['hooks']['PreToolUse']
        ]
        self.assertTrue(any('lessons_gate' in c for c in commands))
        self.assertTrue(any('read_dedupe' in c for c in commands))

