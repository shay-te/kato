"""The agent must not write into the CLI's own state directory.

Reported twice. First as "tell claude/codex/agent that the memory folder will
always be under the task folder — claude is creating files in the global
session location and it's raising warnings", answered with prompt guidance.
Then again, with the warning still firing:

    Claude wrote OUTSIDE the task folder:
      ~/.claude/projects/<encoded-cwd>/memory/una-2742-testing-setup.md
      — no approval was requested

Two things had already failed:

* PROMPT GUIDANCE (``workspace_scope_block`` names the task's own memory
  directory). The CLI's built-in memory feature tells the agent its memory
  lives at a fixed per-user path, and that beats generic instruction.
* The ASK RULES. The CLI treats its own state directory as a scratch path
  and auto-accepts writes there, so the ask never fires — the operator only
  gets a warning AFTER the write, which is a report, not a control.

Deny beats allow and ask — but only a deny that MATCHES. The first version
wrote ``Write(/Users/me/.claude/**)``, and a single leading slash is read
relative to the settings file, so it matched nothing and let every write
through. Verified live against the real CLI with a control in the same run:
``Edit(//Users/me/.claude/**)`` refuses the write, even under
``bypassPermissions``; ``Write(//...)`` does not.
"""
from __future__ import annotations

import os
import unittest

from claude_core_lib.claude_core_lib.helpers.write_scope_settings import (
    agent_state_dir_write_deny_rules,
    out_of_workspace_write_settings,
)


class AgentStateDirIsWriteDeniedTests(unittest.TestCase):
    def _deny(self):
        return out_of_workspace_write_settings('/ws/T1')['permissions']['deny']

    def test_file_writes_are_denied_in_the_agent_state_dir(self) -> None:
        # ONE ``Edit`` rule in the absolute ``//`` form. Both details were
        # verified against the real CLI, with a control write succeeding in
        # the same run: the old per-tool single-slash rules
        # (``Write(/Users/me/.claude/**)``) let writes straight through, and so
        # did ``Write(//...)``; ``Edit(//.../.claude/**)`` refused the write.
        home = os.path.expanduser('~').lstrip('/')
        self.assertIn(f'Edit(//{home}/.claude/**)', self._deny())

    def test_the_rule_covers_the_memory_path_from_the_report(self) -> None:
        # ~/.claude/projects/<encoded-cwd>/memory/... is under ~/.claude, so
        # the recursive glob catches it without naming 'memory' anywhere —
        # the agent picking a different filename cannot slip past.
        home = os.path.expanduser('~').lstrip('/')
        self.assertIn(f'Edit(//{home}/.claude/**)', self._deny())

    def test_no_deny_rule_uses_the_single_slash_path_form(self) -> None:
        # ``Tool(/abs/...)`` is read relative to the settings file and matches
        # nothing — the reason this denial silently never held.
        for rule in self._deny():
            with self.subTest(rule=rule):
                self.assertTrue(rule.split('(', 1)[1].startswith('//'), rule)

    def test_deny_is_present_ALONGSIDE_allow_and_ask(self) -> None:
        # The existing scoping must survive: in-workspace writes still
        # auto-accept, everything else still asks.
        permissions = out_of_workspace_write_settings('/ws/T1')['permissions']
        self.assertTrue(permissions['allow'], 'in-workspace allow-rules lost')
        self.assertTrue(permissions['ask'], 'out-of-workspace ask-rules lost')
        self.assertTrue(permissions['deny'])

    def test_reads_are_denied_ONLY_for_memory(self) -> None:
        # Memory belongs to the task folder, so reading it back out of the
        # per-user directory is refused. Everything else the CLI keeps there
        # stays readable — a broad read denial would be a different restriction
        # nobody asked for.
        home = os.path.expanduser('~')
        read_rules = [
            rule for rule in self._deny()
            if rule.split('(', 1)[0] in ('Read', 'Grep', 'Glob')
        ]
        self.assertTrue(read_rules, 'reading memory outside the task is not denied')
        for rule in read_rules:
            self.assertIn('/memory/**', rule)
        self.assertNotIn(f'Read(//{home.lstrip("/")}/.claude/**)', self._deny())

    def test_the_task_workspace_is_not_caught_by_the_deny(self) -> None:
        # A workspace path must never match the state-dir rule, or the agent
        # could not write its own task at all.
        for rule in agent_state_dir_write_deny_rules():
            self.assertNotIn('/ws/T1', rule)


if __name__ == '__main__':
    unittest.main()
