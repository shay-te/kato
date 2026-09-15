"""The CLI's own memory directory is pinned inside the task folder.

Reported over and over: the operator had to tell the agent, on every task, not
to touch its memory outside the task folder and to keep it inside instead.

Two layers had already been tried and both failed:

* PROMPT GUIDANCE named ``<task>/memory/``. The CLI's built-in memory feature
  tells the agent its memory lives at ``~/.claude/projects/<cwd>/memory/``,
  and that specific built-in instruction wins.
* A WRITE DENIAL on ``~/.claude/**``. It never matched: its rules used the
  single-slash path form, which the CLI reads relative to the settings file.
  Verified live — it let writes straight through. It is fixed now (``Edit(//...)``),
  but a denial only stops the wrong place; it cannot point the agent anywhere.

``autoMemoryDirectory`` fixes it at the source: the CLI itself loads and saves
memory in the task folder. Verified against the real CLI before this was
written — a ``MEMORY.md`` planted in the configured directory was known to the
agent, and unknown without the setting.
"""
from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from agent_core_lib.agent_core_lib.helpers.agent_prompt_utils import (
    WORKSPACES_ROOT_ENV,
)
from claude_core_lib.claude_core_lib.helpers.write_scope_settings import (
    agent_memory_read_deny_rules,
    auto_memory_directory_setting,
    out_of_workspace_write_settings,
    out_of_workspace_write_settings_json,
)

ROOT = os.path.join(os.sep, 'work', 'spaces')
TASK = os.path.join(ROOT, 'PROJ-5')
REPO = os.path.join(TASK, 'payments-api')
TASK_MEMORY = os.path.join(TASK, 'memory')


def _with_root(root=ROOT):
    return patch.dict(os.environ, {WORKSPACES_ROOT_ENV: root})


def _without_root():
    ctx = patch.dict(os.environ, {}, clear=False)
    ctx.start()
    os.environ.pop(WORKSPACES_ROOT_ENV, None)
    return ctx


class AutoMemoryDirectoryTests(unittest.TestCase):
    def test_a_session_in_a_repo_clone_keeps_memory_in_the_task_folder(self) -> None:
        with _with_root():
            settings = out_of_workspace_write_settings(REPO)
        self.assertEqual(settings.get('autoMemoryDirectory'), TASK_MEMORY)

    def test_a_session_in_the_task_folder_gets_the_same_directory(self) -> None:
        with _with_root():
            settings = out_of_workspace_write_settings(TASK)
        self.assertEqual(settings.get('autoMemoryDirectory'), TASK_MEMORY)

    def test_memory_is_never_placed_inside_a_repository_clone(self) -> None:
        # One `git add -A` from being committed and pushed.
        with _with_root():
            memory = out_of_workspace_write_settings(REPO)['autoMemoryDirectory']
        self.assertFalse(memory.startswith(REPO + os.sep))

    def test_two_tasks_never_share_a_memory_directory(self) -> None:
        other = os.path.join(ROOT, 'PROJ-6', 'payments-api')
        with _with_root():
            mine = out_of_workspace_write_settings(REPO)['autoMemoryDirectory']
            theirs = out_of_workspace_write_settings(other)['autoMemoryDirectory']
        self.assertNotEqual(mine, theirs)

    def test_no_known_workspaces_root_leaves_the_cli_default_alone(self) -> None:
        # Never a guess: without the root there is no way to tell a task folder
        # from an arbitrary checkout.
        ctx = _without_root()
        self.addCleanup(ctx.stop)
        self.assertNotIn('autoMemoryDirectory', out_of_workspace_write_settings(REPO))

    def test_a_checkout_outside_the_workspaces_root_gets_no_setting(self) -> None:
        elsewhere = os.path.join(os.sep, 'home', 'dev', 'src', 'some-repo')
        with _with_root():
            self.assertNotIn('autoMemoryDirectory', out_of_workspace_write_settings(elsewhere))

    def test_no_working_directory_gets_no_setting(self) -> None:
        with _with_root():
            self.assertEqual(auto_memory_directory_setting(''), {})

    def test_the_setting_reaches_the_json_the_cli_is_launched_with(self) -> None:
        with _with_root():
            payload = json.loads(out_of_workspace_write_settings_json(REPO))
        self.assertEqual(payload.get('autoMemoryDirectory'), TASK_MEMORY)


class MemoryReadDenyTests(unittest.TestCase):
    def test_reading_memory_under_the_per_user_directory_is_denied(self) -> None:
        # The absolute ``//`` form. Verified live with a control read: the
        # single-slash ``Read(/Users/me/...)`` let the read through and
        # ``Read(//...)`` refused it.
        home = os.path.expanduser('~').lstrip('/')
        deny = out_of_workspace_write_settings(REPO)['permissions']['deny']
        self.assertIn(f'Read(//{home}/.claude/projects/**/memory/**)', deny)

    def test_every_read_denial_is_scoped_to_memory(self) -> None:
        for rule in agent_memory_read_deny_rules():
            self.assertIn('/memory/**', rule)

    def test_the_write_denials_are_still_there(self) -> None:
        home = os.path.expanduser('~').lstrip('/')
        deny = out_of_workspace_write_settings(REPO)['permissions']['deny']
        self.assertIn(f'Edit(//{home}/.claude/**)', deny)

    def test_the_task_memory_directory_is_not_caught_by_any_denial(self) -> None:
        with _with_root():
            settings = out_of_workspace_write_settings(REPO)
        memory = settings['autoMemoryDirectory']
        for rule in settings['permissions']['deny']:
            self.assertNotIn(memory, rule)


if __name__ == '__main__':
    unittest.main()
