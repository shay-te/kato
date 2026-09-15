"""Tests for the out-of-workspace write-approval settings.

The contract: edits INSIDE the task sandbox auto-accept (allow rules), and
a write to ANY path outside it — a sibling repo under the home tree, /tmp,
anywhere — is forced to approval (unscoped ask rules). This closes the hole
where a sibling repo under ``/Users`` matched no enumerated root and slipped
through with no prompt.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from claude_core_lib.claude_core_lib.helpers.write_scope_settings import (
    in_workspace_write_allow_rules,
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
_TASK_FOLDER = '/Users/dev/workspaces/UNA-2763'
_ADD_DIR = '/Users/dev/workspaces/UNA-2763/library-core-lib'
_SIBLING_REPO = '/Users/dev/src/objective_love_core_lib'


class OutOfWorkspaceWriteSettingsTests(unittest.TestCase):
    def test_ask_rules_are_unscoped_catch_all(self) -> None:
        # Bare tool names → every write invocation is a candidate to prompt
        # (the allow rules carve out the in-workspace ones).
        self.assertEqual(out_of_workspace_write_ask_rules(), list(_WRITE_TOOLS))

    def test_allow_rules_cover_cwd_add_dirs_and_task_folder(self) -> None:
        allow = in_workspace_write_allow_rules(_CWD, [_ADD_DIR])
        for tool in _WRITE_TOOLS:
            # cwd repo, a sibling task clone (--add-dir), and the task-folder
            # parent are all auto-accept.
            self.assertIn(f'{tool}({_CWD}/**)', allow)
            self.assertIn(f'{tool}({_ADD_DIR}/**)', allow)
            self.assertIn(f'{tool}({_TASK_FOLDER}/**)', allow)

    def test_sibling_repo_under_home_is_NOT_allowed(self) -> None:
        # The regression: an unrelated repo under the home tree must NOT be
        # allow-listed, so it falls to the catch-all ask and prompts.
        allow = in_workspace_write_allow_rules(_CWD)
        for tool in _WRITE_TOOLS:
            self.assertNotIn(f'{tool}({_SIBLING_REPO}/**)', allow)
        # No allow rule even contains the sibling path.
        for rule in allow:
            self.assertNotIn(_SIBLING_REPO, rule)

    def test_no_workspace_allows_nothing_so_every_write_prompts(self) -> None:
        # Fail-safe: with no cwd (e.g. boot smoke test) there is nothing to
        # allow, so every write is caught by the ask rules.
        self.assertEqual(in_workspace_write_allow_rules('', ()), [])
        settings = out_of_workspace_write_settings('', ())
        self.assertEqual(settings['permissions']['allow'], [])
        self.assertEqual(settings['permissions']['ask'], list(_WRITE_TOOLS))

    def test_settings_shape_is_permissions_allow_ask_deny(self) -> None:
        # ``deny`` joined allow/ask because the ask rules cannot reach the
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
        self.assertEqual(
            sorted(settings['permissions'].keys()), ['allow', 'ask', 'deny'])
        self.assertEqual(
            settings['permissions']['allow'],
            in_workspace_write_allow_rules(_CWD))
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

    The JSON carries one rule per directory, so a 25-repo workspace is ~8KB.
    Inline on the command line that alone reaches the cap ``_build_command``
    warns about, and the multiline system prompt after it is pure overflow —
    the spawn then dies before the agent starts:

        send failed: failed to launch claude CLI binary "claude":
        [WinError 206] The filename or extension is too long

    Reported after a Stop + fresh prompt on a 25-repo task: "the chat is
    completly broken for this task".
    """

    def _many_dirs(self, base: str, count: int = 25):
        return tuple(
            os.path.join(base, f'repo-number-{i:02d}-core-lib') for i in range(count)
        )

    def test_the_path_is_short_where_the_inline_json_is_not(self) -> None:
        base = tempfile.mkdtemp()
        dirs = self._many_dirs(base)
        cwd = os.path.join(base, 'client')

        path = out_of_workspace_write_settings_path(cwd, dirs)
        inline = out_of_workspace_write_settings_json(cwd, dirs)

        self.assertTrue(path)
        self.assertLess(len(path), 300)
        # The thing being avoided: kilobytes of argv per spawn.
        self.assertGreater(len(inline), 3000)

    def test_the_file_holds_exactly_the_same_settings(self) -> None:
        # A shorter command line is worthless if it weakens the write scope.
        base = tempfile.mkdtemp()
        dirs = self._many_dirs(base, 4)
        cwd = os.path.join(base, 'client')

        path = out_of_workspace_write_settings_path(cwd, dirs)
        with open(path, encoding='utf-8') as handle:
            from_file = json.load(handle)

        self.assertEqual(
            from_file, json.loads(out_of_workspace_write_settings_json(cwd, dirs)),
        )

    def test_the_file_is_written_OUTSIDE_the_workspace(self) -> None:
        """This file is what forces out-of-workspace writes to be approved.

        Inside the workspace the agent could edit it and widen its own
        permissions, which is the one place it must not live.
        """
        workspace = tempfile.mkdtemp()
        path = out_of_workspace_write_settings_path(workspace, (workspace,))

        self.assertFalse(
            Path(path).is_relative_to(Path(workspace)),
            f'settings landed inside the agent-writable workspace: {path}',
        )

    def test_two_different_workspaces_do_not_share_a_file(self) -> None:
        a = out_of_workspace_write_settings_path('/tmp/ws-a', ('/tmp/ws-a',))
        b = out_of_workspace_write_settings_path('/tmp/ws-b', ('/tmp/ws-b',))
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
                out_of_workspace_write_settings_path('/tmp/ws', ('/tmp/ws',)), '',
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

    def test_both_hooks_survive_together(self) -> None:
        # Two ``settings.update({'hooks': ...})`` calls would leave only the
        # last one's PreToolUse list — the read-dedupe hook would vanish the
        # moment a lessons file existed.
        with patch.dict(os.environ, {'AGENT_LESSONS_PATH': '/ws/lessons.md'}):
            settings = out_of_workspace_write_settings(
                '/ws', ('/ws',), dedupe_reads=True,
            )
        commands = [
            h['hooks'][0]['command'] for h in settings['hooks']['PreToolUse']
        ]
        self.assertTrue(any('lessons_gate' in c for c in commands))
        self.assertTrue(any('read_dedupe' in c for c in commands))

