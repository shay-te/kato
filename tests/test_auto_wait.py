"""``kato:auto-wait``: Auto mode, the work, the review loop — then the operator's Done.

Real stores on disk (``KATO_AUTOPILOT_PATH`` / ``KATO_PLAN_MODE_PATH`` in a temp
folder), the real ``AgentService``, ``WaitPlanningService`` and
``AutopilotFinisher``; collaborators are plain recorders, and the Done button's
call is a recorder that must never be reached. No mocks.

Pinned: the tag is read (auto-wait wins over autopilot) and remembered per task;
the task starts in Auto and its agent is told to finish by itself; the park
starts the review loop (default rounds, or the ticket's own) even with
auto-push on; neither the park, the done token nor a CLEAN loop presses Done;
the tag removed releases it; the tasks rail gets the level.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from kato_core_lib.data_layers.data.fields import TaskTags
from kato_core_lib.data_layers.data.sentinels import KATO_TASK_DONE_SENTINEL
from kato_core_lib.data_layers.data.task import Task
from kato_core_lib.data_layers.service.agent_service import AgentService
from kato_core_lib.data_layers.service.autopilot_finish import AutopilotFinisher
from kato_core_lib.data_layers.service.wait_planning_service import WaitPlanningService
from kato_core_lib.helpers import autopilot_store
from kato_core_lib.helpers.autopilot_guidance_utils import (
    AUTO_WAIT_GUIDANCE,
    AUTO_WAIT_GUIDANCE_NO_TESTS,
    AUTOPILOT_GUIDANCE,
    with_autopilot_guidance,
)
from kato_core_lib.helpers.autopilot_store import (
    AUTO_WAIT,
    AUTOPILOT,
    automation_level,
    automation_levels,
    is_auto_wait,
    is_autopilot,
    runs_by_itself,
    set_automation_level,
)
from kato_core_lib.helpers.kato_tag_utils import DEFAULT_MAX_ROUNDS, automation_level_from_tags
from kato_core_lib.helpers.plan_mode_store import AUTOPILOT_MODE, DEFAULT_MODE_ENV, unpicked_task_mode
from kato_core_lib.helpers.task_context_utils import PreparedTaskContext

REPO_ROOT = Path(__file__).resolve().parent.parent


class _Isolated(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)
        self.store = self.folder / 'autopilot_tasks.json'
        env = patch.dict(os.environ, {
            'KATO_AUTOPILOT_PATH': str(self.store),
            'KATO_PLAN_MODE_PATH': str(self.folder / 'plan_mode.json'),
        })
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop(DEFAULT_MODE_ENV, None)


class TagTests(unittest.TestCase):
    def test_the_tag_is_in_kato_namespace(self) -> None:
        self.assertEqual(TaskTags.AUTO_WAIT, 'kato:auto-wait')

    def test_auto_wait_whatever_its_case_and_auto_wait_wins_over_autopilot(self) -> None:
        for tags in (['kato:auto-wait'], ['  KATO:Auto-Wait '], ['kato:autopilot', 'kato:auto-wait']):
            with self.subTest(tags=tags):
                self.assertEqual(automation_level_from_tags(tags), AUTO_WAIT)

    def test_autopilot_alone_and_nothing(self) -> None:
        self.assertEqual(automation_level_from_tags(['kato:autopilot']), AUTOPILOT)
        for tags in (None, [], ['kato:auto-wait:on'], ['auto-wait'], [None]):
            with self.subTest(tags=tags):
                self.assertEqual(automation_level_from_tags(tags), '')


class StoreTests(_Isolated):
    def test_each_level_is_remembered_and_released(self) -> None:
        self.assertEqual(automation_level('UNA-1'), '')
        self.assertTrue(set_automation_level('UNA-1', AUTO_WAIT))
        self.assertTrue(is_auto_wait('una-1'))
        self.assertFalse(is_autopilot('UNA-1'))
        self.assertTrue(runs_by_itself('UNA-1'))
        self.assertFalse(set_automation_level('UNA-1', AUTO_WAIT))  # no change
        self.assertTrue(set_automation_level('UNA-1', AUTOPILOT))
        self.assertTrue(is_autopilot('UNA-1'))
        self.assertTrue(set_automation_level('UNA-1', ''))
        self.assertFalse(runs_by_itself('UNA-1'))
        self.assertEqual(automation_levels(), {})

    def test_an_unknown_level_releases_it(self) -> None:
        set_automation_level('UNA-1', AUTO_WAIT)
        self.assertTrue(set_automation_level('UNA-1', 'turbo'))
        self.assertEqual(automation_level('UNA-1'), '')

    def test_kept_on_disk_as_task_to_level(self) -> None:
        import json
        set_automation_level('UNA-2', AUTO_WAIT)
        set_automation_level('UNA-1', AUTOPILOT)
        self.assertEqual(
            json.loads(self.store.read_text(encoding='utf-8')),
            {'una-1': AUTOPILOT, 'una-2': AUTO_WAIT},
        )
        self.assertEqual(automation_levels(), {'una-1': AUTOPILOT, 'una-2': AUTO_WAIT})

    def test_a_file_from_before_auto_wait_reads_as_autopilot(self) -> None:
        self.store.write_text('["UNA-1", "", 7]', encoding='utf-8')
        self.assertTrue(is_autopilot('una-1'))
        self.assertEqual(automation_levels(), {'una-1': AUTOPILOT})

    def test_damaged_entries_are_nobody(self) -> None:
        for text in ('not json', '"una-1"', '{"una-1": true, "": "auto-wait", "7": 7}'):
            with self.subTest(text=text):
                self.store.write_text(text, encoding='utf-8')
                self.assertEqual(automation_levels(), {})

    def test_no_task_id_records_nothing(self) -> None:
        self.assertFalse(set_automation_level('  ', AUTO_WAIT))
        self.assertEqual(automation_level(''), '')
        self.assertFalse(self.store.exists())


class ModeAndGuidanceTests(_Isolated):
    def test_an_auto_wait_task_starts_in_auto(self) -> None:
        self.assertNotEqual(unpicked_task_mode('UNA-1'), AUTOPILOT_MODE)
        set_automation_level('UNA-1', AUTO_WAIT)
        self.assertEqual(unpicked_task_mode('UNA-1'), AUTOPILOT_MODE)

    def test_its_agent_finishes_by_itself_and_is_told_nothing_is_pushed(self) -> None:
        set_automation_level('UNA-1', AUTO_WAIT)
        prompt = with_autopilot_guidance('UNA-1', 'kato guidance')
        self.assertTrue(prompt.startswith('kato guidance\n\n## Auto-wait'))
        self.assertIn(KATO_TASK_DONE_SENTINEL, prompt)
        self.assertIn('nothing is pushed', prompt)
        self.assertNotIn('presses Done itself', prompt)
        self.assertEqual(with_autopilot_guidance('UNA-1', ''), AUTO_WAIT_GUIDANCE)

    def test_tests_off_drops_its_tests_pass(self) -> None:
        set_automation_level('UNA-1', AUTO_WAIT)
        with patch.dict(os.environ, {'KATO_RUN_TESTS': 'false'}):
            self.assertEqual(with_autopilot_guidance('UNA-1', ''), AUTO_WAIT_GUIDANCE_NO_TESTS)
        self.assertNotIn('tests pass', AUTO_WAIT_GUIDANCE_NO_TESTS)

    def test_autopilot_keeps_its_own_words(self) -> None:
        set_automation_level('UNA-1', AUTOPILOT)
        self.assertEqual(with_autopilot_guidance('UNA-1', ''), AUTOPILOT_GUIDANCE)
        self.assertIn('presses Done itself', AUTOPILOT_GUIDANCE)


class _Logs(object):
    """A logger stand-in that keeps what was said."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def info(self, message, *args) -> None:
        self.lines.append(message % args if args else message)

    warning = info
    error = info

    def exception(self, message, *args) -> None:
        self.lines.append('EXC ' + (message % args if args else message))


class ObserveTests(_Isolated):
    def _planning(self) -> WaitPlanningService:
        service = WaitPlanningService.__new__(WaitPlanningService)
        service._track_planning_holds = True
        service.logger = _Logs()
        return service

    def test_the_tag_on_the_ticket_switches_it_on_and_off(self) -> None:
        planning = self._planning()
        planning.observe_autopilot(Task(id='UNA-1', tags=[TaskTags.AUTO_WAIT, TaskTags.AUTOPILOT]))
        self.assertTrue(is_auto_wait('UNA-1'))
        planning.observe_autopilot(Task(id='UNA-1', tags=[TaskTags.AUTOPILOT]))
        self.assertTrue(is_autopilot('UNA-1'))
        planning.observe_autopilot(Task(id='UNA-1', tags=['kato:repo:api']))
        self.assertEqual(automation_level('UNA-1'), '')
        self.assertEqual(planning.logger.lines, [
            'task UNA-1 is now on auto-wait',
            'task UNA-1 is now on autopilot',
            'task UNA-1 is no longer on autopilot or auto-wait',
        ])

    def test_a_store_that_cannot_be_written_is_logged_not_raised(self) -> None:
        self.store.mkdir()  # a folder where the file should be: the write fails for real
        planning = self._planning()
        planning.observe_autopilot(Task(id='UNA-1', tags=[TaskTags.AUTO_WAIT]))
        self.assertEqual(planning.logger.lines, ['EXC failed to record the automation tag for task UNA-1'])


class _Recorder(object):
    def __init__(self, result=None) -> None:
        self.calls: list[tuple] = []
        self._result = result

    def __call__(self, *args):
        self.calls.append(args)
        return self._result


class _Tickets(object):
    def __init__(self, tasks: dict[str, Task]) -> None:
        self.tasks = tasks
        self.comments: list[tuple[str, str]] = []

    def get_task(self, task_id):
        return self.tasks.get(task_id)

    def add_comment(self, task_id, body) -> None:
        self.comments.append((task_id, body))


class _Workspaces(object):
    def __init__(self) -> None:
        self.statuses: list[tuple[str, str]] = []

    def update_status(self, task_id, status) -> None:
        self.statuses.append((task_id, status))


class _MustNotRun(object):
    def __init__(self, name: str) -> None:
        self._name = name

    def __getattr__(self, attribute):
        raise AssertionError(f'{self._name}.{attribute} was used')


class AgentServiceTests(_Isolated):
    """The real AgentService: the park, the done token and the loop's end."""

    def _agent(self, *tasks: Task) -> AgentService:
        self.tickets = _Tickets({task.id: task for task in tasks})
        self.logger = logging.getLogger(f'test_auto_wait.{self.id()}')
        agent = AgentService(
            task_service=self.tickets,
            task_state_service=_MustNotRun('task_state_service'),
            implementation_service=_MustNotRun('implementation_service'),
            testing_service=_MustNotRun('testing_service'),
            repository_service=_MustNotRun('repository_service'),
            notification_service=_MustNotRun('notification_service'),
            workspace_manager=_Workspaces(),
            logger=self.logger,
        )
        self.loops = _Recorder()
        agent.parked_task_listener = self.loops
        self.done = _Recorder({'finished': True})
        agent._autopilot._press_done = self.done  # the Done button's call: never reached
        agent._autopilot._background = lambda work, name: work()
        return agent

    def _park(self, agent: AgentService, task: Task) -> dict:
        prepared = PreparedTaskContext(repositories=[], repository_branches={}, branch_name=task.id)
        return agent._pause_for_push_approval(task, prepared, {'success': True})

    def test_the_park_starts_the_review_loop_with_the_default_rounds_and_presses_nothing(self) -> None:
        set_automation_level('UNA-1', AUTO_WAIT)
        task = Task(id='UNA-1', tags=[TaskTags.AUTO_WAIT])
        agent = self._agent(task)
        with self.assertLogs(self.logger, level='INFO') as logs:
            result = self._park(agent, task)
        self.assertEqual(self.loops.calls, [('UNA-1', DEFAULT_MAX_ROUNDS)])
        self.assertEqual(self.done.calls, [])
        self.assertNotIn('autopilot', result)
        self.assertTrue(any('auto-wait: the review loop runs; Done is yours' in line for line in logs.output))

    def test_the_ticket_s_own_rounds_win(self) -> None:
        set_automation_level('UNA-1', AUTO_WAIT)
        task = Task(id='UNA-1', tags=[TaskTags.AUTO_WAIT, 'kato:review-loop:2'])
        agent = self._agent(task)
        self._park(agent, task)
        self.assertEqual(self.loops.calls, [('UNA-1', 2)])

    def test_a_clean_loop_does_not_press_done(self) -> None:
        set_automation_level('UNA-1', AUTO_WAIT)
        agent = self._agent()
        with self.assertLogs(self.logger, level='INFO') as logs:
            agent._autopilot.review_loop_finished('UNA-1', 'clean')
            agent._autopilot.review_loop_finished('UNA-1', '')
        self.assertEqual(self.done.calls, [])
        said = [line for line in logs.output if 'auto-wait' in line]
        self.assertIn('the review loop ended clean — the task waits for you to press Done', said[0])
        self.assertIn('ended without a verdict', said[1])

    def test_the_done_token_from_a_chat_starts_the_loop_and_never_publishes(self) -> None:
        set_automation_level('UNA-1', AUTO_WAIT)
        agent = self._agent(Task(id='UNA-1', tags=[TaskTags.AUTO_WAIT]))
        agent._review_loop_service = SimpleNamespace(is_running=lambda task_id: False)
        with patch.object(AgentService, 'publish', new=_MustNotRun('publish')):
            self.assertIsNone(agent.finish_from_done_marker('UNA-1'))
        self.assertEqual(self.loops.calls, [('UNA-1', DEFAULT_MAX_ROUNDS)])
        self.assertEqual(self.done.calls, [])

    def test_the_done_token_during_kato_s_own_run_waits_for_the_park(self) -> None:
        set_automation_level('UNA-1', AUTO_WAIT)
        agent = self._agent()
        agent._autopilot._runner_in_flight = lambda task_id: True
        with self.assertLogs(self.logger, level='INFO') as logs:
            self.assertTrue(agent._autopilot.agent_said_done('UNA-1'))
        self.assertEqual(self.loops.calls, [])
        self.assertTrue(any('the review loop runs once kato\'s own run parks' in line for line in logs.output))

    def test_an_unreadable_task_starts_no_loop_and_publishes_nothing(self) -> None:
        set_automation_level('UNA-1', AUTO_WAIT)
        agent = self._agent()  # the tracker does not have it
        with self.assertLogs(self.logger, level='INFO') as logs:
            self.assertTrue(agent._autopilot.agent_said_done('UNA-1'))
        self.assertEqual(self.loops.calls, [])
        self.assertTrue(any('could not read the task, so its review loop did not start' in line
                            for line in logs.output))

    def test_a_tracker_that_raises_is_logged(self) -> None:
        set_automation_level('UNA-1', AUTO_WAIT)
        agent = self._agent()

        def refuse(task_id):
            raise RuntimeError('tracker offline')

        agent._autopilot._read_task = refuse
        with self.assertLogs(self.logger, level='ERROR') as logs:
            self.assertTrue(agent._autopilot.agent_said_done('UNA-1'))
        self.assertIn('auto-wait: could not read task UNA-1', logs.output[0])

    def test_auto_push_on_still_parks_an_auto_wait_task(self) -> None:
        set_automation_level('UNA-1', AUTO_WAIT)
        with patch('kato_core_lib.data_layers.service.agent_service.auto_push_enabled', return_value=True):
            self.assertTrue(AgentService._should_pause_for_push_approval(Task(id='UNA-1', tags=[])))
            self.assertFalse(AgentService._should_pause_for_push_approval(Task(id='UNA-2', tags=[])))

    def test_no_tag_no_loop(self) -> None:
        agent = self._agent()
        self.assertEqual(agent._review_loop_rounds(Task(id='UNA-9', tags=[])), 0)

    def test_autopilot_still_presses_done(self) -> None:
        set_automation_level('UNA-1', AUTOPILOT)
        agent = self._agent()
        agent._autopilot.review_loop_finished('UNA-1', 'clean')
        self.assertEqual(self.done.calls, [('UNA-1',)])


class FinisherOffTests(_Isolated):
    def test_no_level_changes_nothing(self) -> None:
        done = _Recorder()
        finisher = AutopilotFinisher(
            press_done=done, read_task=_MustNotRun('read_task'), review_rounds=_MustNotRun('rounds'),
            start_review_loop=_MustNotRun('loop'), holds_push=_MustNotRun('holds'),
            runner_in_flight=_MustNotRun('runner'), logger=_Logs(),
        )
        self.assertFalse(finisher.agent_said_done('UNA-1'))
        finisher.review_loop_finished('UNA-1', 'clean')
        self.assertEqual(done.calls, [])


class TasksRailTests(_Isolated):
    def test_the_tab_list_carries_each_task_s_level(self) -> None:
        from kato_webserver import app as webapp
        set_automation_level('UNA-1', AUTO_WAIT)
        set_automation_level('UNA-2', AUTOPILOT)
        levels = webapp._automation_levels()
        for task_id, expected in (('UNA-1', AUTO_WAIT), ('una-2', AUTOPILOT), ('UNA-3', '')):
            with self.subTest(task=task_id):
                payload = webapp._workspace_record_to_dict(
                    SimpleNamespace(task_id=task_id, to_dict=lambda: {}), set(), automation_by_task=levels,
                )
                self.assertEqual(payload['automation'], expected)

    def test_an_unreadable_store_colours_nothing(self) -> None:
        from kato_webserver import app as webapp

        def broken():
            raise OSError('unreadable')

        with patch.object(autopilot_store, 'automation_levels', broken):
            self.assertEqual(webapp._automation_levels(), {})

    def test_the_ui_reads_the_same_level_names(self) -> None:
        source = (REPO_ROOT / 'webserver/ui/src/constants/automationLevel.js').read_text(encoding='utf-8')
        self.assertEqual(
            dict(re.findall(r"(\w+): '([\w-]+)'", source)),
            {'AUTOPILOT': AUTOPILOT, 'AUTO_WAIT': AUTO_WAIT},
        )


if __name__ == '__main__':
    unittest.main()
