"""Read-dedupe hook — blocks re-reads of a file the agent already has.

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
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from claude_core_lib.claude_core_lib.helpers import read_dedupe
from claude_core_lib.claude_core_lib.helpers.write_scope_settings import (
    out_of_workspace_write_settings,
    read_dedupe_enabled,
)


class ReadDedupeDecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix='read-dedupe-')
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.state = self.root / 'state'
        self.target = self.root / 'module.py'
        self.target.write_text('print(1)\n', encoding='utf-8')
        patcher = patch.dict(os.environ, {
            read_dedupe.STATE_DIR_ENV: str(self.state),
            read_dedupe.WINDOW_ENV: '900',
        })
        patcher.start()
        self.addCleanup(patcher.stop)

    def _payload(self, **input_overrides):
        tool_input = {'file_path': str(self.target)}
        tool_input.update(input_overrides)
        return {
            'session_id': 'sess-1', 'tool_name': 'Read', 'tool_input': tool_input,
        }

    def test_first_read_is_served(self) -> None:
        self.assertIsNone(read_dedupe.decide(self._payload()))

    def test_second_read_of_an_unchanged_file_is_blocked(self) -> None:
        read_dedupe.decide(self._payload())
        decision = read_dedupe.decide(self._payload())
        self.assertIsNotNone(decision)
        hook = decision['hookSpecificOutput']
        self.assertEqual(hook['permissionDecision'], 'deny')
        self.assertIn(str(self.target), hook['permissionDecisionReason'])
        # The reason must name the escape hatch, or the agent has no way out.
        self.assertIn('offset', hook['permissionDecisionReason'])

    def test_a_changed_file_is_served_again(self) -> None:
        read_dedupe.decide(self._payload())
        self.target.write_text('print(2)\nprint(3)\n', encoding='utf-8')
        self.assertIsNone(read_dedupe.decide(self._payload()))

    def test_a_ranged_read_is_always_served(self) -> None:
        read_dedupe.decide(self._payload())
        self.assertIsNone(read_dedupe.decide(self._payload(offset=10, limit=40)))
        self.assertIsNone(read_dedupe.decide(self._payload(limit=40)))

    def test_a_read_outside_the_window_is_served(self) -> None:
        # The window is the only protection against suppressing a file a
        # compaction has dropped — this hook cannot see compaction.
        now = 1_000_000.0
        read_dedupe.decide(self._payload(), now=now)
        self.assertIsNone(read_dedupe.decide(self._payload(), now=now + 901))

    def test_the_window_runs_from_the_last_SERVE_not_the_last_ask(self) -> None:
        # Otherwise a file asked for repeatedly stays suppressed forever,
        # long past any compaction that dropped it.
        now = 1_000_000.0
        read_dedupe.decide(self._payload(), now=now)
        self.assertIsNotNone(read_dedupe.decide(self._payload(), now=now + 500))
        self.assertIsNone(read_dedupe.decide(self._payload(), now=now + 901))

    def test_a_different_session_is_independent(self) -> None:
        read_dedupe.decide(self._payload())
        other = self._payload()
        other['session_id'] = 'sess-2'
        self.assertIsNone(read_dedupe.decide(other))

    def test_other_tools_are_untouched(self) -> None:
        payload = self._payload()
        payload['tool_name'] = 'Edit'
        self.assertIsNone(read_dedupe.decide(payload))
        self.assertIsNone(read_dedupe.decide(payload))

    def test_a_missing_file_is_served(self) -> None:
        payload = self._payload(file_path=str(self.root / 'gone.py'))
        self.assertIsNone(read_dedupe.decide(payload))

    def test_malformed_payloads_are_served(self) -> None:
        for payload in (None, {}, {'tool_name': 'Read'},
                        {'tool_name': 'Read', 'tool_input': 'nope'},
                        {'tool_name': 'Read', 'tool_input': {'file_path': ''}}):
            self.assertIsNone(read_dedupe.decide(payload))

    def test_no_state_dir_configured_serves_everything(self) -> None:
        # Fail open: with nowhere to remember what was served, the hook must
        # never guess that the agent already has a file.
        with patch.dict(os.environ, {read_dedupe.STATE_DIR_ENV: ''}):
            self.assertIsNone(read_dedupe.decide(self._payload()))
            self.assertIsNone(read_dedupe.decide(self._payload()))

    def test_an_unreadable_state_file_serves_everything(self) -> None:
        read_dedupe.decide(self._payload())
        for path in self.state.glob('*.json'):
            path.write_text('{not json', encoding='utf-8')
        self.assertIsNone(read_dedupe.decide(self._payload()))

    def test_state_is_capped(self) -> None:
        now = 1_000_000.0
        for index in range(read_dedupe._MAX_TRACKED_FILES + 40):
            target = self.root / f'f{index}.py'
            target.write_text('x\n', encoding='utf-8')
            read_dedupe.decide(
                {'session_id': 'cap', 'tool_name': 'Read',
                 'tool_input': {'file_path': str(target)}},
                now=now + index,
            )
        state = read_dedupe._load(read_dedupe._state_path('cap'))
        self.assertLessEqual(len(state), read_dedupe._MAX_TRACKED_FILES)


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
