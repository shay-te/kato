"""Wiring the read-dedupe hook into Claude's ``--settings`` payload.

The hook's own logic (and the measurement behind it) lives in
``agent_core_lib.helpers.read_dedupe`` — both transports can use it. What is
Claude-specific, and what these tests guard, is the ``PreToolUse`` settings
block that installs it and the opt-in switch that gates it.

Original rationale:

The measurement behind it (two real task transcripts, 4,565 tool calls): 704
``Read`` calls covered 193 distinct files, and 23.9% of the read payload was
re-reading an UNCHANGED file with no compaction in between — content that was
still in the context window. ~173k tokens, ~13% of everything the tools put
into context.

What these tests really guard is the other direction: every uncertain case
must SERVE the file. Starving the agent of context it genuinely lost produces
confidently wrong edits, which costs far more than the tokens saved.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from claude_core_lib.claude_core_lib.helpers.write_scope_settings import (
    out_of_workspace_write_settings,
    read_dedupe_enabled,
)




class ReadDedupeSettingsTests(unittest.TestCase):
    def test_off_by_default(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('AGENT_READ_DEDUPE_ENABLED', None)
            self.assertFalse(read_dedupe_enabled())
            self.assertNotIn('hooks', out_of_workspace_write_settings('/w'))

    def test_enabled_by_the_generic_env_switch(self) -> None:
        with patch.dict(os.environ, {'AGENT_READ_DEDUPE_ENABLED': 'true'}):
            settings = out_of_workspace_write_settings('/w')
        hook = settings['hooks']['PreToolUse'][0]
        self.assertEqual(hook['matcher'], 'Read')
        self.assertIn('read_dedupe', hook['hooks'][0]['command'])

    def test_an_explicit_argument_overrides_the_env(self) -> None:
        with patch.dict(os.environ, {'AGENT_READ_DEDUPE_ENABLED': 'true'}):
            self.assertNotIn(
                'hooks', out_of_workspace_write_settings('/w', (), dedupe_reads=False),
            )

    def test_the_write_rules_are_unchanged_by_the_hook(self) -> None:
        with patch.dict(os.environ, {'AGENT_READ_DEDUPE_ENABLED': 'true'}):
            settings = out_of_workspace_write_settings('/w')
        self.assertIn('Write(/w/**)', settings['permissions']['allow'])
        self.assertIn('Write', settings['permissions']['ask'])


if __name__ == '__main__':
    unittest.main()
