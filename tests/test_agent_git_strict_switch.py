"""Settings → Claude agent → "Strict git": kato's side of the switch.

On (unset, the default): the agent edits files and kato makes every commit.
Off: the agent may also run the history of its own task branch. The levels
themselves are agent_core_lib's (``command_floor``) and are tested there; this
pins how kato reads the setting, that every chat spawn carries it fresh, how
it is offered in Settings, and that a remembered approval keeps a hard reset
apart from a soft one.

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

from kato_core_lib.data_layers.service.planning_session_runner import (
    PlanningSessionRunner,
    StreamingSessionDefaults,
)
from kato_core_lib.helpers.agent_git_access_utils import (
    AGENT_GIT_STRICT_KEY as KEY,
    agent_git_access,
)
from kato_core_lib.helpers.kato_settings_schema_utils import (
    DEFAULT_ON_BOOL_KEYS,
    schema_for_api,
)
from kato_core_lib.helpers.tool_decision_utils import command_programs_of
from tests.test_planning_session_runner import _FakeManager, _terminal


class _SettingsFile(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.settings_path = Path(tmp.name) / 'settings.json'
        patcher = patch.dict(os.environ, {'KATO_SETTINGS_FILE': str(self.settings_path)})
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(KEY, None)

    def _save(self, value: str) -> None:
        self.settings_path.write_text(json.dumps({KEY: value}), encoding='utf-8')


class ReadingTheSettingTests(_SettingsFile):
    def test_unset_is_strict(self) -> None:
        self.assertEqual(agent_git_access({}), 'strict')

    def test_only_an_explicit_off_turns_it_off(self) -> None:
        for value in ('false', 'False', ' off ', '0', 'no'):
            with self.subTest(value=value):
                self._save(value)
                self.assertEqual(agent_git_access({}), 'local')
        for value in ('true', '1', 'yes', 'maybe', 'local', 'flase'):
            with self.subTest(value=value):
                self._save(value)
                self.assertEqual(agent_git_access({}), 'strict')

    def test_the_settings_file_wins_over_the_shell(self) -> None:
        self.assertEqual(agent_git_access({KEY: 'false'}), 'local')
        self._save('true')
        self.assertEqual(agent_git_access({KEY: 'false'}), 'strict')

    def test_a_corrupt_settings_file_cannot_turn_it_off(self) -> None:
        self.settings_path.write_text('{nope', encoding='utf-8')
        self.assertEqual(agent_git_access({}), 'strict')


class EverySpawnCarriesItTests(_SettingsFile):
    """Read at the runner's one spawn funnel, so a save reaches the next spawn."""

    def _runner(self, manager) -> PlanningSessionRunner:
        return PlanningSessionRunner(
            session_manager=manager,
            defaults=StreamingSessionDefaults(binary='claude', permission_mode='acceptEdits'),
        )

    def _spawn(self, runner: PlanningSessionRunner) -> None:
        runner.start_session(
            task_id='PROJ-1', task_summary='s', initial_prompt='go', cwd='/tmp/client',
        )

    def test_a_save_reaches_the_next_spawn_without_a_restart(self) -> None:
        manager = _FakeManager(_terminal(result='ok'))
        runner = self._runner(manager)
        self._spawn(runner)
        self.assertEqual(manager.start_kwargs['git_access'], 'strict')
        self._save('false')
        self._spawn(runner)
        self.assertEqual(manager.start_kwargs['git_access'], 'local')

    def test_the_chat_spawn_carries_it(self) -> None:
        self._save('false')
        manager = _FakeManager(_terminal(result='ok'))
        self._runner(manager).resume_session_for_chat(
            task_id='PROJ-1', message='commit this', cwd='/tmp/client', task_summary='s',
        )
        self.assertEqual(manager.start_kwargs['git_access'], 'local')


class SettingsDrawerTests(unittest.TestCase):
    def _field(self) -> dict:
        for section in schema_for_api():
            for field in section['fields']:
                if field['key'] == KEY:
                    return {**field, 'section': section['id']}
        self.fail(f'{KEY} is not offered in Settings')

    def test_it_is_a_switch_that_draws_on_when_unset(self) -> None:
        field = self._field()
        self.assertEqual(field['type'], 'bool')
        self.assertEqual(field['label'], 'Strict git')
        self.assertIn(KEY, DEFAULT_ON_BOOL_KEYS)

    def test_it_is_global_and_lives_in_its_own_tab(self) -> None:
        # In General (not the Claude section): it governs Codex too. The
        # ``panel`` is the bespoke tab's id — how the drawer and search find it.
        field = self._field()
        self.assertEqual(field['section'], 'general')
        self.assertEqual(field['panel'], 'strict-git')

    def test_turning_it_off_is_confirmed_in_a_popup(self) -> None:
        confirm = self._field()['confirm']
        self.assertEqual(confirm['when'], 'false')
        self.assertTrue(confirm['title'])
        self.assertTrue(any('push' in line.lower() for line in confirm['effects']))

    def test_it_says_what_off_allows_and_what_it_never_allows(self) -> None:
        help_text = self._field()['help'].lower()
        for words in ('commit', 'rebase', 'only kato pushes', 'claude and codex', 'auto'):
            self.assertIn(words, help_text, words)


class RememberedApprovalKeyTests(unittest.TestCase):
    def test_a_hard_reset_is_remembered_apart_from_a_soft_one(self) -> None:
        soft = command_programs_of('git reset --soft HEAD~1')
        hard = command_programs_of('git reset --hard origin/main')
        self.assertEqual(soft, ['git reset'])
        self.assertEqual(hard, ['git reset --hard'])

    def test_other_git_keys_are_unchanged(self) -> None:
        self.assertEqual(command_programs_of('git commit -m x'), ['git commit'])
        self.assertEqual(command_programs_of('git restore .'), ['git restore (whole tree)'])


if __name__ == '__main__':
    unittest.main()
