"""Settings → Automation: switches for what kato does on its own in a task.

"Have an option that kato will not run any tests when doing the task, and all
the options to turn on and off what kato does automatically." Each switch is
read fresh (no restart) and is ON unless switched off. These pin, through the
real code that each gates:

* Run tests off — kato's testing step is skipped (any backend), every chat
  kato starts is told not to run tests, autopilot stops saying "its tests
  pass", and a review loop runs no test stage;
* Stop containers on Done off — a finished task's containers keep running;
* both, plus the switches that were already read fresh, save without a
  "restart required";
* the tab offers every switch, drawn on when unset where kato treats it so.

The settings file is redirected to a tmpfile per test.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from kato_core_lib.data_layers.data.task import Task
from kato_core_lib.data_layers.service.agent_service import AgentService
from kato_core_lib.data_layers.service.planning_session_runner import (
    PlanningSessionRunner,
    StreamingSessionDefaults,
)
from kato_core_lib.data_layers.service.task_containers import TaskContainers
from kato_core_lib.helpers.automation_switches_utils import (
    RUN_TESTS_KEY,
    STOP_CONTAINERS_ON_DONE_KEY,
    run_tests_enabled,
    stop_containers_on_done,
)
from kato_core_lib.helpers.autopilot_guidance_utils import (
    AUTOPILOT_GUIDANCE,
    AUTOPILOT_GUIDANCE_NO_TESTS,
    with_autopilot_guidance,
)
from kato_core_lib.helpers.kato_settings_schema_utils import DEFAULT_ON_BOOL_KEYS, schema_for_api
from kato_core_lib.helpers.run_tests_utils import NO_TESTS_GUIDANCE, with_test_policy
from kato_core_lib.helpers.task_context_utils import PreparedTaskContext
from tests.test_agent_service_more_coverage import _kwargs
from tests.test_planning_session_runner import _FakeManager, _terminal


class _SettingsFile(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.settings_path = Path(tmp.name) / 'settings.json'
        patcher = patch.dict(os.environ, {'KATO_SETTINGS_FILE': str(self.settings_path)})
        patcher.start()
        self.addCleanup(patcher.stop)
        for key in (RUN_TESTS_KEY, STOP_CONTAINERS_ON_DONE_KEY):
            os.environ.pop(key, None)

    def _save(self, **values) -> None:
        self.settings_path.write_text(json.dumps(values), encoding='utf-8')


class SwitchReadingTests(_SettingsFile):
    def test_on_unless_switched_off(self) -> None:
        self.assertTrue(run_tests_enabled({}))
        self.assertTrue(stop_containers_on_done({}))
        for value in ('false', 'Off', '0', 'no'):
            with self.subTest(value=value):
                self._save(**{RUN_TESTS_KEY: value, STOP_CONTAINERS_ON_DONE_KEY: value})
                self.assertFalse(run_tests_enabled({}))
                self.assertFalse(stop_containers_on_done({}))
        self._save(**{RUN_TESTS_KEY: 'flase'})
        self.assertTrue(run_tests_enabled({}), 'a typo keeps the behaviour kato always had')


class TestingStepTests(_SettingsFile):
    def _run(self, testing_service):
        service = AgentService(**_kwargs(testing_service=testing_service))
        prepared = PreparedTaskContext(repositories=[], repository_branches={}, branch_name='b')
        return service._run_task_testing_validation(Task(id='T1'), prepared, {'success': True})

    def test_run_tests_off_skips_kato_testing_step_on_any_backend(self) -> None:
        self._save(**{RUN_TESTS_KEY: 'false'})
        testing = MagicMock()
        ok, result, _ = self._run(testing)
        self.assertTrue(ok)
        self.assertIsNone(result)
        testing.test_task.assert_not_called()

    def test_run_tests_on_still_tests(self) -> None:
        testing = MagicMock()
        testing.test_task.side_effect = RuntimeError('ran')
        self._run(testing)
        testing.test_task.assert_called_once()


class ChatPromptTests(_SettingsFile):
    def _spawned_prompt(self) -> str:
        manager = _FakeManager(_terminal(result='ok'))
        PlanningSessionRunner(
            session_manager=manager,
            defaults=StreamingSessionDefaults(binary='claude', permission_mode='acceptEdits'),
        ).start_session(task_id='PROJ-1', task_summary='s', initial_prompt='go', cwd='/tmp/client')
        return manager.start_kwargs['extra_system_prompt']

    def test_every_chat_is_told_not_to_run_tests_and_a_save_reaches_the_next_spawn(self) -> None:
        self.assertNotIn(NO_TESTS_GUIDANCE, self._spawned_prompt())
        self._save(**{RUN_TESTS_KEY: 'false'})
        prompt = self._spawned_prompt()
        self.assertIn(NO_TESTS_GUIDANCE, prompt)
        self.assertIn('overrides any other instruction to run tests', prompt)

    def test_the_policy_helper(self) -> None:
        self.assertEqual(with_test_policy('G'), 'G')
        self._save(**{RUN_TESTS_KEY: 'false'})
        self.assertEqual(with_test_policy('G'), f'G\n\n{NO_TESTS_GUIDANCE}')
        self.assertEqual(with_test_policy(''), NO_TESTS_GUIDANCE)

    def test_autopilot_no_longer_waits_for_tests_it_must_not_run(self) -> None:
        with patch('kato_core_lib.helpers.autopilot_guidance_utils.automation_level', return_value='autopilot'):
            self.assertIn(AUTOPILOT_GUIDANCE, with_autopilot_guidance('T1', 'G'))
            self._save(**{RUN_TESTS_KEY: 'false'})
            guidance = with_autopilot_guidance('T1', 'G')
        self.assertIn(AUTOPILOT_GUIDANCE_NO_TESTS, guidance)
        self.assertNotIn('tests pass', guidance)


class ReviewLoopTests(_SettingsFile):
    def test_run_tests_off_drops_the_test_stage_whatever_was_asked(self) -> None:
        from kato_webserver import review_loop_routes

        stages = {'self_check': False, 'verify_tests': True, 'confirm_clean': True, 'extra_sweep': True}
        self.assertEqual(review_loop_routes._with_test_policy(stages), stages)
        self._save(**{RUN_TESTS_KEY: 'false'})
        forced = review_loop_routes._with_test_policy(stages)
        self.assertFalse(forced['verify_tests'])
        self.assertTrue(forced['confirm_clean'])
        self.assertTrue(stages['verify_tests'], 'the caller\'s dict is not changed')


class ContainersOnDoneTests(_SettingsFile):
    def _done(self) -> MagicMock:
        later = MagicMock()
        TaskContainers(workspace_manager=MagicMock(), containers=MagicMock(), run_later=later).task_done('T1')
        return later

    def test_done_stops_them_by_default(self) -> None:
        self._done().assert_called_once()

    def test_switched_off_they_keep_running(self) -> None:
        self._save(**{STOP_CONTAINERS_ON_DONE_KEY: 'false'})
        self._done().assert_not_called()


class SettingsTabTests(unittest.TestCase):
    _EXPECTED = {
        'KATO_RUN_TESTS', 'KATO_STOP_CONTAINERS_ON_DONE', 'KATO_SECURITY_SCANNER_ENABLED',
        'KATO_AUTO_PUSH_ENABLED', 'KATO_PUBLISH_FOLLOWS_PROJECT_CONVENTIONS',
        'KATO_RELEASE_NOTES_ENABLED', 'KATO_COMPLETION_EMAIL_ENABLED', 'KATO_FAILURE_EMAIL_ENABLED',
        'KATO_TASK_COMMENTS_ENABLED', 'KATO_TASK_COMMENTS_REQUIRE_MENTION',
        'KATO_REVIEW_COMMENTS_ENABLED', 'KATO_REVIEW_COMMENTS_REQUIRE_MENTION',
    }

    def test_the_automation_tab_offers_every_switch(self) -> None:
        tagged = {
            field['key'] for section in schema_for_api() for field in section['fields']
            if field.get('panel') == 'automation'
        }
        self.assertEqual(tagged, self._EXPECTED)

    def test_switches_kato_runs_by_default_are_drawn_on(self) -> None:
        for key in ('KATO_RUN_TESTS', 'KATO_STOP_CONTAINERS_ON_DONE',
                    'KATO_RELEASE_NOTES_ENABLED', 'KATO_SECURITY_SCANNER_ENABLED'):
            self.assertIn(key, DEFAULT_ON_BOOL_KEYS, key)

    def test_read_fresh_switches_save_without_a_restart(self) -> None:
        from kato_webserver import app as app_module

        live = app_module._live_settings_keys()
        for key in ('KATO_RUN_TESTS', 'KATO_STOP_CONTAINERS_ON_DONE', 'KATO_AGENT_GIT_STRICT',
                    'KATO_AUTO_PUSH_ENABLED', 'KATO_PUBLISH_FOLLOWS_PROJECT_CONVENTIONS'):
            self.assertIn(key, live, key)
        message = app_module._apply_live_settings(MagicMock(), {'KATO_RUN_TESTS': 'false'})
        self.assertIn('no restart', message)


if __name__ == '__main__':
    unittest.main()
