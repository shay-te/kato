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

Deny beats allow and ask, so it is the only layer that actually holds.
"""
from __future__ import annotations

import os
import unittest

from claude_core_lib.claude_core_lib.helpers.write_scope_settings import (
    agent_state_dir_write_deny_rules,
    out_of_workspace_write_settings,
)

_WRITE_TOOLS = ('Write', 'Edit', 'MultiEdit', 'NotebookEdit')


class AgentStateDirIsWriteDeniedTests(unittest.TestCase):
    def _deny(self):
        return out_of_workspace_write_settings('/ws/T1')['permissions']['deny']

    def test_every_write_tool_is_denied_in_the_agent_state_dir(self) -> None:
        home = os.path.expanduser('~')
        for tool in _WRITE_TOOLS:
            with self.subTest(tool=tool):
                self.assertIn(f'{tool}({home}/.claude/**)', self._deny())

    def test_the_rule_covers_the_memory_path_from_the_report(self) -> None:
        # ~/.claude/projects/<encoded-cwd>/memory/... is under ~/.claude, so
        # the recursive glob catches it without naming 'memory' anywhere —
        # the agent picking a different filename cannot slip past.
        home = os.path.expanduser('~')
        rule = f'Write({home}/.claude/**)'
        self.assertIn(rule, self._deny())

    def test_deny_is_present_ALONGSIDE_allow_and_ask(self) -> None:
        # The existing scoping must survive: in-workspace writes still
        # auto-accept, everything else still asks.
        permissions = out_of_workspace_write_settings('/ws/T1')['permissions']
        self.assertTrue(permissions['allow'], 'in-workspace allow-rules lost')
        self.assertTrue(permissions['ask'], 'out-of-workspace ask-rules lost')
        self.assertTrue(permissions['deny'])

    def test_reads_are_NOT_denied(self) -> None:
        # The agent may still consult whatever the CLI put there; only
        # writing it is refused.
        joined = ' '.join(self._deny())
        self.assertNotIn('Read(', joined)
        self.assertNotIn('Grep(', joined)
        self.assertNotIn('Glob(', joined)

    def test_the_task_workspace_is_not_caught_by_the_deny(self) -> None:
        # A workspace path must never match the state-dir rule, or the agent
        # could not write its own task at all.
        for rule in agent_state_dir_write_deny_rules():
            self.assertNotIn('/ws/T1', rule)


if __name__ == '__main__':
    unittest.main()
