"""The agent's memory must land inside the task folder.

Reported from real sessions: the agent wrote its memory files into the
per-user agent directory (the global sessions/projects location its CLI
points at), which sits outside the task folder — so every write tripped the
out-of-folder approval and interrupted the operator for something they never
needed to decide.

Generic guidance was not enough. The scope block already said notes and
"memory you want on the next turn" belong in the helper-script directory, but
a CLI with a built-in memory feature tells the agent its memory lives at a
fixed path, and a specific instruction beats a general one. The fix is to
name the task-local directory AND to say plainly that it overrides whatever
the CLI said.
"""

from __future__ import annotations

import os
import unittest

from agent_core_lib.agent_core_lib.helpers.agent_prompt_utils import (
    workspace_scope_block,
)


class MemoryDirectoryGuidanceTests(unittest.TestCase):
    def _block(self, workspace='/work/UNA-1'):
        # A LIST of allowed paths — the first is the task folder the
        # helper/memory directories hang off.
        return workspace_scope_block([workspace])

    def test_it_names_a_memory_directory_under_the_task_folder(self) -> None:
        block = self._block('/work/UNA-1')
        expected = f'/work/UNA-1{os.sep}memory{os.sep}'
        self.assertIn('YOUR MEMORY DIRECTORY IS:', block)
        self.assertIn(expected, block)

    def test_the_path_follows_the_task_folder(self) -> None:
        # Not a constant tucked somewhere — it has to be THIS task's folder,
        # or two tasks would share one memory.
        block = self._block('/elsewhere/UNA-9')
        self.assertIn(f'/elsewhere/UNA-9{os.sep}memory{os.sep}', block)
        self.assertNotIn('/work/UNA-1', block)

    def test_it_says_the_cli_s_own_memory_path_is_overridden(self) -> None:
        # The whole point: the agent has already been told, by its own CLI,
        # that its memory is somewhere else. Saying "put notes here" without
        # addressing that loses to the more specific instruction.
        block = self._block().lower()
        self.assertIn('overrides', block)

    def test_it_rules_out_the_global_agent_directory_by_name(self) -> None:
        # A prohibition the agent can recognise itself in: these are the
        # shapes a per-user memory path actually takes.
        block = self._block().lower()
        for phrase in ('home directory', 'config directory'):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, block)

    def test_the_memory_guidance_names_no_vendor(self) -> None:
        # Scoped to the memory paragraph on purpose. The surrounding block
        # has a pre-existing ``.claude`` mention (a directory name the agent
        # creates, not a claim about which agent is running); asserting over
        # the whole block would fail on that rather than on this guidance.
        block = self._block()
        start = block.index('YOUR MEMORY DIRECTORY IS:')
        end = block.index('This boundary is about', start)
        memory = block[start:end].lower()
        # Agent vendors only. The host product's own name is covered by
        # tests/test_corelib_agnostic_gate, and naming it here would itself
        # be the leak that gate exists to catch — it did catch it.
        for brand in ('claude', 'codex', 'openhands'):
            with self.subTest(brand=brand):
                self.assertNotIn(brand, memory)


if __name__ == '__main__':
    unittest.main()
