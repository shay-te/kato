"""Tests for the out-of-workspace write-approval settings.

The contract: edits INSIDE the task sandbox auto-accept (allow rules), and
a write to ANY path outside it — a sibling repo under the home tree, /tmp,
anywhere — is forced to approval (unscoped ask rules). This closes the hole
where a sibling repo under ``/Users`` matched no enumerated root and slipped
through with no prompt.
"""
from __future__ import annotations

import json
import unittest

from claude_core_lib.claude_core_lib.helpers.write_scope_settings import (
    in_workspace_write_allow_rules,
    agent_state_dir_write_deny_rules,
    out_of_workspace_write_ask_rules,
    out_of_workspace_write_settings,
    out_of_workspace_write_settings_json,
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
            settings['permissions']['deny'], agent_state_dir_write_deny_rules())

    def test_json_is_valid_and_compact(self) -> None:
        raw = out_of_workspace_write_settings_json(_CWD)
        self.assertNotIn(', ', raw)  # compact separators
        self.assertEqual(json.loads(raw), out_of_workspace_write_settings(_CWD))


if __name__ == '__main__':
    unittest.main()
