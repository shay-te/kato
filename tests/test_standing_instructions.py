"""Standing instructions (Settings → Prompts): the operator's own text in every
task's prompt.

It rides in the system prompt — the same ``--append-system-prompt`` that names
the lessons file — after kato's own guidance, so it holds for the whole task
(a resumed chat never sees a first message again). Read fresh at every spawn,
so a save applies to the next chat without restarting kato.

The settings file is redirected to a tmpfile per test so nothing reads the
operator's real ``~/.kato/settings.json``.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_core_lib.agent_core_lib.helpers.agent_prompt_utils import (
    WORKSPACES_ROOT_ENV,
)
from claude_core_lib.claude_core_lib.session.streaming import (
    StreamingClaudeSession,
)
from kato_core_lib.helpers.kato_settings_schema_utils import (
    schema_for_api,
    validate_settings_values,
)
from kato_core_lib.helpers import standing_instructions_utils
from kato_core_lib.helpers.kato_settings_store_utils import live_setting
from kato_core_lib.helpers.plan_writer import PLAN_PROGRESS_GUIDANCE
from kato_core_lib.helpers.standing_instructions_utils import (
    STANDING_INSTRUCTIONS_KEY as KEY,
    STANDING_INSTRUCTIONS_MAX_CHARS,
    standing_instructions,
    with_standing_instructions,
)

_HEADING = '# Standing instructions from the operator'
_RULE = 'Every new endpoint gets an integration test.\nNever edit vendor/.'


class _SettingsFile(unittest.TestCase):
    """Points ``kato_settings_path()`` at a per-test tmpfile."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.settings_path = self.root / 'settings.json'
        patcher = patch.dict(os.environ, {'KATO_SETTINGS_FILE': str(self.settings_path)})
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(KEY, None)

    def _save(self, **values) -> None:
        self.settings_path.write_text(json.dumps(values), encoding='utf-8')


class StandingInstructionsTests(_SettingsFile):

    def test_nothing_written_leaves_kato_guidance_untouched(self) -> None:
        guidance = PLAN_PROGRESS_GUIDANCE
        self.assertIs(with_standing_instructions(guidance, {}), guidance)

    def test_the_text_follows_kato_guidance_under_its_own_heading(self) -> None:
        self._save(**{KEY: _RULE})
        prompt = with_standing_instructions(PLAN_PROGRESS_GUIDANCE, {})
        self.assertTrue(prompt.startswith(PLAN_PROGRESS_GUIDANCE.rstrip()))
        self.assertLess(prompt.index(_HEADING), prompt.index(_RULE))
        self.assertTrue(prompt.endswith(_RULE), prompt[-80:])

    def test_a_save_applies_to_the_very_next_read(self) -> None:
        self._save(**{KEY: _RULE})
        self.assertIn(_RULE, with_standing_instructions('G', {}))
        # Cleared in Settings: gone on the next spawn, no restart.
        self._save(**{KEY: ''})
        self.assertEqual(with_standing_instructions('G', {}), 'G')

    def test_settings_json_wins_over_the_shell(self) -> None:
        shell = {KEY: 'from the shell'}
        self.assertEqual(standing_instructions(shell), 'from the shell')
        self._save(**{KEY: 'from settings'})
        self.assertEqual(standing_instructions(shell), 'from settings')

    def test_blank_text_adds_nothing(self) -> None:
        self._save(**{KEY: '  \n\t '})
        self.assertEqual(with_standing_instructions('G', {}), 'G')

    def test_without_kato_guidance_it_is_the_whole_block(self) -> None:
        self._save(**{KEY: _RULE})
        prompt = with_standing_instructions('', {})
        self.assertTrue(prompt.startswith(_HEADING), prompt[:80])
        self.assertTrue(prompt.endswith(_RULE))

    def test_a_hand_edited_text_past_the_cap_is_cut_and_logged(self) -> None:
        self._save(**{KEY: 'x' * (STANDING_INSTRUCTIONS_MAX_CHARS + 50)})
        with self.assertLogs(standing_instructions_utils._logger, level='WARNING') as logs:
            text = standing_instructions({})
        self.assertEqual(len(text), STANDING_INSTRUCTIONS_MAX_CHARS)
        self.assertIn(KEY, '\n'.join(logs.output))


class LiveSettingTests(_SettingsFile):

    def test_settings_file_then_shell_then_blank(self) -> None:
        self.assertEqual(live_setting('SOME_KEY', {}), '')
        self.assertEqual(live_setting('SOME_KEY', {'SOME_KEY': ' shell '}), 'shell')
        self._save(SOME_KEY=' file ')
        self.assertEqual(live_setting('SOME_KEY', {'SOME_KEY': 'shell'}), 'file')

    def test_a_corrupt_settings_file_falls_back_to_the_shell(self) -> None:
        self.settings_path.write_text('{nope', encoding='utf-8')
        self.assertEqual(live_setting('SOME_KEY', {'SOME_KEY': 'shell'}), 'shell')


class SettingsSchemaTests(unittest.TestCase):

    def test_it_is_a_textarea_shown_on_the_prompts_tab(self) -> None:
        fields = {
            field['key']: field
            for section in schema_for_api() if section['id'] == 'general'
            for field in section['fields']
        }
        field = fields[KEY]
        self.assertEqual(field['type'], 'textarea')
        self.assertEqual(field['label'], 'Standing instructions')
        self.assertEqual(field['panel'], 'prompts')
        self.assertEqual(field['max_length'], STANDING_INSTRUCTIONS_MAX_CHARS)

    def test_a_save_past_the_cap_is_refused(self) -> None:
        errors = validate_settings_values({KEY: 'x' * (STANDING_INSTRUCTIONS_MAX_CHARS + 1)})
        self.assertEqual(len(errors), 1)
        self.assertIn(KEY, errors[0])
        self.assertIn(f'{STANDING_INSTRUCTIONS_MAX_CHARS:,}', errors[0])

    def test_multi_line_text_up_to_the_cap_is_accepted(self) -> None:
        self.assertEqual(validate_settings_values({KEY: _RULE}), [])
        self.assertEqual(
            validate_settings_values({KEY: 'x' * STANDING_INSTRUCTIONS_MAX_CHARS}), [],
        )


class SystemPromptTests(_SettingsFile):
    """Through the real streaming command: what the agent is actually given."""

    def _system_prompt(self, extra_system_prompt: str, lessons_path: str) -> str:
        session = StreamingClaudeSession(
            task_id='PROJ-1', cwd=str(self.root),
            lessons_path=lessons_path, extra_system_prompt=extra_system_prompt,
        )
        with patch.dict(os.environ, {WORKSPACES_ROOT_ENV: ''}), patch(
            'shutil.which', return_value='/usr/local/bin/claude',
        ):
            command = session._build_command()
        return command[command.index('--append-system-prompt') + 1]

    def test_they_come_after_the_lessons_directive_and_kato_guidance(self) -> None:
        lessons = self.root / 'lessons.md'
        lessons.write_text('# Lessons\n- keep it small\n', encoding='utf-8')
        self._save(**{KEY: _RULE})

        prompt = self._system_prompt(
            with_standing_instructions(PLAN_PROGRESS_GUIDANCE), str(lessons),
        )

        self.assertLess(prompt.index(str(lessons)), prompt.index('# Plans end with'))
        self.assertLess(prompt.index('# Plans end with'), prompt.index(_HEADING))
        self.assertTrue(prompt.endswith(_RULE), prompt[-80:])


if __name__ == '__main__':
    unittest.main()
