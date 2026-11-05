"""``kato:autopilot``: the task starts in Auto and kato presses Done when it ends.

Done is the operator's own button — ``finish_task_planning_session(task,
by_operator=True)`` — never a publish of autopilot's own. These tests pin:
the tag is read and remembered per task; the spawn AND the composer's picker
show Auto unless the operator picked a mode; the agent is told to finish by
itself; and each "the work is finished" moment (the agent's done token, the
park after tests, a review loop's end) presses Done exactly when it should —
never past a ``kato:wait-before-git-push`` tag, an unread task, or a loop that
did not end clean.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, PropertyMock, patch

from kato_core_lib.data_layers.data.fields import TaskTags
from kato_core_lib.data_layers.data.sentinels import KATO_TASK_DONE_SENTINEL
from kato_core_lib.data_layers.data.task import Task
from kato_core_lib.data_layers.service.agent_service import AgentService
from kato_core_lib.data_layers.service.autopilot_finish import AutopilotFinisher
from kato_core_lib.data_layers.service.planning_session_runner import _task_mode_spawn
from kato_core_lib.data_layers.service.review_loop_adapters import _loop_observer
from kato_core_lib.data_layers.service.wait_planning_service import WaitPlanningService
from kato_core_lib.helpers.autopilot_guidance_utils import (
    AUTOPILOT_GUIDANCE,
    with_autopilot_guidance,
)
from kato_core_lib.helpers.autopilot_store import is_autopilot, set_autopilot
from kato_core_lib.helpers.kato_tag_utils import has_autopilot_tag
from kato_core_lib.helpers.plan_mode_store import (
    AUTOPILOT_MODE,
    DEFAULT_MODE_ENV,
    EDIT_AUTOMATICALLY_MODE,
    picked_mode_to_store,
    set_task_mode,
    task_permission_mode,
    unpicked_task_mode,
)
from kato_core_lib.helpers.task_context_utils import PreparedTaskContext


class _Isolated(unittest.TestCase):
    """Every per-task store in a temp folder; no default mode configured."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)
        env = patch.dict(os.environ, {
            'KATO_AUTOPILOT_PATH': str(self.folder / 'autopilot_tasks.json'),
            'KATO_PLAN_MODE_PATH': str(self.folder / 'plan_mode.json'),
        })
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop(DEFAULT_MODE_ENV, None)


class TagTests(unittest.TestCase):

    def test_the_tag_is_read_whatever_its_case_or_spacing(self) -> None:
        for tags in ([TaskTags.AUTOPILOT], ['  KATO:Autopilot '], ['kato:repo:api', 'kato:autopilot']):
            with self.subTest(tags=tags):
                self.assertTrue(has_autopilot_tag(tags))

    def test_anything_else_is_not_the_tag(self) -> None:
        for tags in (None, [], ['kato:autopilot:on'], ['autopilot'], ['kato:review-loop'], [None]):
            with self.subTest(tags=tags):
                self.assertFalse(has_autopilot_tag(tags))

    def test_the_tag_is_in_kato_namespace(self) -> None:
        self.assertEqual(TaskTags.AUTOPILOT, 'kato:autopilot')


class StoreTests(_Isolated):

    def test_a_task_is_remembered_and_released(self) -> None:
        self.assertFalse(is_autopilot('UNA-1'))
        self.assertTrue(set_autopilot('UNA-1', True))
        self.assertTrue(is_autopilot('una-1'))  # ids match in any case
        self.assertFalse(set_autopilot('UNA-1', True))  # no change
        self.assertTrue(set_autopilot('una-1', False))
        self.assertFalse(is_autopilot('UNA-1'))

    def test_it_survives_a_restart(self) -> None:
        set_autopilot('UNA-1', True)
        import json
        self.assertEqual(json.loads((self.folder / 'autopilot_tasks.json').read_text(encoding='utf-8')), ['una-1'])

    def test_no_task_id_records_nothing(self) -> None:
        self.assertFalse(set_autopilot('  ', True))
        self.assertFalse(is_autopilot(''))
        self.assertFalse((self.folder / 'autopilot_tasks.json').exists())

    def test_a_damaged_file_reads_as_nobody_on_autopilot(self) -> None:
        for text in ('not json', '{"una-1": true}', '[1, null]'):
            with self.subTest(text=text):
                (self.folder / 'autopilot_tasks.json').write_text(text, encoding='utf-8')
                self.assertFalse(is_autopilot('una-1'))


class ModeTests(_Isolated):

    def test_a_task_on_autopilot_starts_in_auto(self) -> None:
        set_autopilot('UNA-1', True)
        self.assertEqual(AUTOPILOT_MODE, 'bypassPermissions')
        self.assertEqual(task_permission_mode('UNA-1'), AUTOPILOT_MODE)
        self.assertEqual(unpicked_task_mode('UNA-1'), AUTOPILOT_MODE)
        self.assertEqual(_task_mode_spawn('UNA-1')['permission_mode'], AUTOPILOT_MODE)

    def test_autopilot_outranks_the_default_mode_for_new_tasks(self) -> None:
        set_autopilot('UNA-1', True)
        with patch.dict(os.environ, {DEFAULT_MODE_ENV: 'plan'}):
            self.assertEqual(task_permission_mode('UNA-1'), AUTOPILOT_MODE)
            self.assertEqual(task_permission_mode('UNA-2'), 'plan')

    def test_the_operators_pick_wins(self) -> None:
        set_autopilot('UNA-1', True)
        set_task_mode('UNA-1', 'plan')
        self.assertEqual(task_permission_mode('UNA-1'), 'plan')

    def test_picking_edit_automatically_sticks_on_autopilot(self) -> None:
        # '' would read back as Auto, so the pick is kept explicitly.
        set_autopilot('UNA-1', True)
        stored = picked_mode_to_store('', 'UNA-1')
        self.assertEqual(stored, EDIT_AUTOMATICALLY_MODE)
        set_task_mode('UNA-1', stored)
        self.assertEqual(task_permission_mode('UNA-1'), EDIT_AUTOMATICALLY_MODE)
        self.assertEqual(picked_mode_to_store('', 'UNA-2'), '')

    def test_off_autopilot_nothing_changes(self) -> None:
        self.assertEqual(task_permission_mode('UNA-1'), '')
        self.assertEqual(_task_mode_spawn('UNA-1'), {})

    def test_the_composer_shows_the_mode_that_will_run(self) -> None:
        from kato_webserver.app import _task_mode_of
        app = SimpleNamespace(config={'TASK_PLAN_MODE_OVERRIDES': {'UNA-2': 'plan'}})
        set_autopilot('UNA-1', True)
        set_autopilot('UNA-2', True)
        self.assertEqual(_task_mode_of(app, 'UNA-1'), AUTOPILOT_MODE)
        self.assertEqual(_task_mode_of(app, 'UNA-2'), 'plan')
        self.assertEqual(_task_mode_of(app, 'UNA-3'), '')


class GuidanceTests(_Isolated):

    def test_a_task_on_autopilot_is_told_to_finish_by_itself(self) -> None:
        set_autopilot('UNA-1', True)
        prompt = with_autopilot_guidance('UNA-1', 'kato guidance')
        self.assertTrue(prompt.startswith('kato guidance\n\n## Autopilot'))
        self.assertIn(KATO_TASK_DONE_SENTINEL, prompt)
        self.assertIn('Never print it while work is left, nor in a review fix turn', prompt)
        self.assertEqual(with_autopilot_guidance('UNA-1', ''), AUTOPILOT_GUIDANCE)

    def test_other_tasks_keep_their_prompt_unchanged(self) -> None:
        self.assertEqual(with_autopilot_guidance('UNA-1', 'kato guidance'), 'kato guidance')

    def test_the_spawn_funnel_adds_it(self) -> None:
        from kato_core_lib.data_layers.service import planning_session_runner
        source = Path(planning_session_runner.__file__).read_text(encoding='utf-8')
        self.assertIn(
            'extra_system_prompt=with_autopilot_guidance(task_id, with_standing_instructions(',
            source,
        )


class ObserveTests(_Isolated):

    def _planning(self, track: bool = True) -> WaitPlanningService:
        service = WaitPlanningService.__new__(WaitPlanningService)
        service._track_planning_holds = track
        service.logger = MagicMock()
        return service

    def test_the_tag_on_the_ticket_switches_it_on_and_off(self) -> None:
        planning = self._planning()
        planning.observe_autopilot(Task(id='UNA-1', tags=[TaskTags.AUTOPILOT]))
        self.assertTrue(is_autopilot('UNA-1'))
        planning.observe_autopilot(Task(id='UNA-1', tags=['kato:repo:api']))
        self.assertFalse(is_autopilot('UNA-1'))
        self.assertEqual(planning.logger.info.call_count, 2)

    def test_untracked_holds_record_nothing(self) -> None:
        self._planning(track=False).observe_autopilot(Task(id='UNA-1', tags=[TaskTags.AUTOPILOT]))
        self.assertFalse(is_autopilot('UNA-1'))

    def test_every_sighting_of_a_task_observes_it(self) -> None:
        planning = self._planning()
        planning.observe_planning_hold = MagicMock()
        planning.observe_model_tag = MagicMock()
        planning.sync_planning_holds([Task(id='UNA-1', tags=[TaskTags.AUTOPILOT])])
        self.assertTrue(is_autopilot('UNA-1'))
        planning._hold_mode = MagicMock(return_value='')
        self.assertIsNone(planning.handle_task(Task(id='UNA-1', tags=[])))
        self.assertFalse(is_autopilot('UNA-1'))


def _finisher(**overrides) -> tuple[AutopilotFinisher, dict]:
    calls = dict(
        press_done=MagicMock(return_value={'finished': True, 'moved_to_review': True}),
        read_task=MagicMock(side_effect=lambda task_id: Task(id=task_id, tags=[])),
        review_rounds=MagicMock(return_value=0),
        start_review_loop=MagicMock(),
        holds_push=MagicMock(return_value=False),
        runner_in_flight=MagicMock(return_value=False),
        logger=MagicMock(),
        run_in_background=MagicMock(side_effect=lambda work, name: work()),
    )
    calls.update(overrides)
    return AutopilotFinisher(**calls), calls


class FinisherTests(_Isolated):

    def setUp(self) -> None:
        super().setUp()
        set_autopilot('UNA-1', True)

    def said(self, calls: dict) -> list[str]:
        return [call.args[2] for call in calls['logger'].info.call_args_list]

    # -- the agent printed the done token --

    def test_off_autopilot_the_caller_publishes_as_before(self) -> None:
        finisher, calls = _finisher()
        self.assertFalse(finisher.agent_said_done('UNA-2'))
        calls['press_done'].assert_not_called()
        calls['read_task'].assert_not_called()

    def test_no_review_loop_presses_done(self) -> None:
        finisher, calls = _finisher()
        self.assertTrue(finisher.agent_said_done(' UNA-1 '))
        calls['press_done'].assert_called_once_with('UNA-1')
        self.assertIn('autopilot: Done pressed — the task moved to In Review', self.said(calls))

    def test_a_review_loop_runs_first(self) -> None:
        finisher, calls = _finisher(review_rounds=MagicMock(return_value=5))
        self.assertTrue(finisher.agent_said_done('UNA-1'))
        calls['start_review_loop'].assert_called_once()
        self.assertEqual(calls['start_review_loop'].call_args.args[0].id, 'UNA-1')
        calls['press_done'].assert_not_called()

    def test_a_run_kato_owns_presses_done_at_its_park_not_now(self) -> None:
        finisher, calls = _finisher(runner_in_flight=MagicMock(return_value=True))
        self.assertTrue(finisher.agent_said_done('UNA-1'))
        calls['press_done'].assert_not_called()
        calls['read_task'].assert_not_called()

    def test_an_unreadable_task_presses_nothing(self) -> None:
        for read in (MagicMock(return_value=None), MagicMock(side_effect=RuntimeError('tracker down'))):
            with self.subTest(read=read):
                finisher, calls = _finisher(read_task=read)
                self.assertTrue(finisher.agent_said_done('UNA-1'))
                calls['press_done'].assert_not_called()
                calls['start_review_loop'].assert_not_called()
                self.assertIn('press it yourself', self.said(calls)[-1])

    def test_wait_before_push_leaves_it_to_the_operator(self) -> None:
        finisher, calls = _finisher(holds_push=MagicMock(return_value=True))
        self.assertTrue(finisher.agent_said_done('UNA-1'))
        calls['press_done'].assert_not_called()
        calls['start_review_loop'].assert_not_called()

    # -- kato's own run parked after its tests --

    def test_a_parked_task_without_a_loop_presses_done(self) -> None:
        finisher, calls = _finisher()
        result = finisher.task_parked(Task(id='UNA-1'))
        self.assertEqual(result, {'finished': True, 'moved_to_review': True})
        calls['press_done'].assert_called_once_with('UNA-1')

    def test_a_parked_task_with_a_loop_waits_for_it(self) -> None:
        finisher, calls = _finisher(review_rounds=MagicMock(return_value=3))
        self.assertIsNone(finisher.task_parked(Task(id='UNA-1')))
        calls['press_done'].assert_not_called()
        calls['start_review_loop'].assert_not_called()  # the park started it

    def test_a_parked_task_off_autopilot_or_held_stays_parked(self) -> None:
        finisher, calls = _finisher()
        self.assertIsNone(finisher.task_parked(Task(id='UNA-2')))
        held, held_calls = _finisher(holds_push=MagicMock(return_value=True))
        self.assertIsNone(held.task_parked(Task(id='UNA-1')))
        calls['press_done'].assert_not_called()
        held_calls['press_done'].assert_not_called()

    # -- a review loop ended --

    def test_a_clean_loop_presses_done_in_the_background(self) -> None:
        from review_loop_core_lib.review_loop_core_lib.data.state import ReviewLoopStatus
        finisher, calls = _finisher(run_in_background=MagicMock())
        finisher.review_loop_finished('UNA-1', ReviewLoopStatus.CLEAN)
        calls['press_done'].assert_not_called()
        work, name = calls['run_in_background'].call_args.args
        self.assertEqual(name, 'autopilot-done-UNA-1')
        work()
        calls['press_done'].assert_called_once_with('UNA-1')

    def test_any_other_ending_leaves_it_to_the_operator(self) -> None:
        from review_loop_core_lib.review_loop_core_lib.data.state import ReviewLoopStatus
        for status in ReviewLoopStatus:
            if status is ReviewLoopStatus.CLEAN:
                continue
            with self.subTest(status=status):
                finisher, calls = _finisher()
                finisher.review_loop_finished('UNA-1', status)
                calls['press_done'].assert_not_called()
                self.assertIn('the task is yours', self.said(calls)[-1])

    def test_a_loop_off_autopilot_changes_nothing(self) -> None:
        finisher, calls = _finisher()
        finisher.review_loop_finished('UNA-2', 'clean')
        calls['press_done'].assert_not_called()
        calls['logger'].info.assert_not_called()

    # -- pressing Done --

    def test_a_done_that_does_not_finish_says_why(self) -> None:
        finisher, calls = _finisher(press_done=MagicMock(return_value={
            'finished': False, 'move_error': 'push and pull-request creation both failed',
        }))
        finisher.press('UNA-1')
        self.assertIn('Done did not finish the task: push and pull-request creation both failed', self.said(calls)[-1])

    def test_a_done_that_raises_is_logged_not_raised(self) -> None:
        finisher, calls = _finisher(press_done=MagicMock(side_effect=RuntimeError('boom')))
        self.assertIsNone(finisher.press('UNA-1'))
        calls['logger'].exception.assert_called_once()
        self.assertIn('Done failed', self.said(calls)[-1])

    def test_a_sub_task_says_it_moved_to_done(self) -> None:
        finisher, calls = _finisher(press_done=MagicMock(return_value={'finished': True, 'moved_to_done': True}))
        finisher.press('UNA-1')
        self.assertIn('the task moved to Done', self.said(calls)[-1])


def _agent(**overrides) -> AgentService:
    kwargs = dict(
        task_service=MagicMock(),
        task_state_service=MagicMock(),
        implementation_service=MagicMock(),
        testing_service=MagicMock(),
        repository_service=MagicMock(),
        notification_service=MagicMock(),
        workspace_manager=MagicMock(),
    )
    kwargs.update(overrides)
    agent = AgentService(**kwargs)
    agent.parked_task_listener = MagicMock()
    return agent


class AgentServiceTests(_Isolated):
    """The real wiring: each moment reaches the Done button's own call."""

    def setUp(self) -> None:
        super().setUp()
        set_autopilot('UNA-1', True)
        self.publish = MagicMock()
        self.publish.finish_task_planning_session.return_value = {'finished': True}
        patcher = patch.object(AgentService, 'publish', new_callable=PropertyMock, return_value=self.publish)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_done_token_presses_the_operators_done(self) -> None:
        agent = _agent()
        agent._task_service.get_task.return_value = Task(id='UNA-1', tags=[TaskTags.AUTOPILOT])
        agent.finish_from_done_marker('UNA-1')
        self.publish.finish_task_planning_session.assert_called_once_with('UNA-1', by_operator=True)

    def test_off_autopilot_the_done_token_is_unchanged(self) -> None:
        agent = _agent()
        agent.finish_from_done_marker('UNA-2')
        self.publish.finish_task_planning_session.assert_called_once_with('UNA-2')

    def test_the_done_token_starts_the_tagged_review_loop_instead(self) -> None:
        agent = _agent()
        agent._task_service.get_task.return_value = Task(id='UNA-1', tags=['kato:review-loop:4'])
        agent.finish_from_done_marker('UNA-1')
        agent.parked_task_listener.assert_called_once_with('UNA-1', 4)
        self.publish.finish_task_planning_session.assert_not_called()

    def test_the_done_token_during_kato_own_run_waits_for_the_park(self) -> None:
        runner = MagicMock()
        runner.is_in_flight.return_value = True
        agent = _agent(parallel_task_runner=runner)
        agent.finish_from_done_marker('UNA-1')
        self.publish.finish_task_planning_session.assert_not_called()
        runner.is_in_flight.assert_called_with('UNA-1')

    def test_the_done_token_during_a_review_loop_is_still_ignored(self) -> None:
        agent = _agent()
        agent._review_loop_service = MagicMock()
        agent._review_loop_service.is_running.return_value = True
        agent.finish_from_done_marker('UNA-1')
        self.publish.finish_task_planning_session.assert_not_called()

    def _park(self, agent: AgentService, *tags: str) -> dict:
        prepared = PreparedTaskContext(repositories=[], repository_branches={}, branch_name='UNA-1')
        return agent._pause_for_push_approval(Task(id='UNA-1', tags=list(tags)), prepared, {'success': True})

    def test_the_park_presses_done(self) -> None:
        agent = _agent()
        result = self._park(agent, TaskTags.AUTOPILOT)
        self.publish.finish_task_planning_session.assert_called_once_with('UNA-1', by_operator=True)
        self.assertEqual(result['autopilot'], {'finished': True})

    def test_the_park_with_a_review_loop_starts_it_and_waits(self) -> None:
        agent = _agent()
        result = self._park(agent, TaskTags.AUTOPILOT, TaskTags.REVIEW_LOOP)
        agent.parked_task_listener.assert_called_once_with('UNA-1', 5)
        self.publish.finish_task_planning_session.assert_not_called()
        self.assertNotIn('autopilot', result)

    def test_the_park_with_wait_before_push_stays_parked(self) -> None:
        agent = _agent()
        self._park(agent, TaskTags.AUTOPILOT, TaskTags.WAIT_BEFORE_GIT_PUSH)
        self.publish.finish_task_planning_session.assert_not_called()
        self.assertIn('UNA-1', agent._pending_publish)

    def test_a_clean_review_loop_presses_done(self) -> None:
        from review_loop_core_lib.review_loop_core_lib.data.state import ReviewLoopStatus
        agent = _agent()
        agent._autopilot._background = lambda work, name: work()
        observe = _loop_observer(lambda: agent.logger, agent._autopilot.review_loop_finished)
        with patch('kato_core_lib.data_layers.service.review_loop_adapters.log_review_loop_event'):
            observe(SimpleNamespace(task_id='UNA-1', status=ReviewLoopStatus.CLEAN), 'finished')
        self.publish.finish_task_planning_session.assert_called_once_with('UNA-1', by_operator=True)

    def test_the_review_loop_service_tells_the_finisher_when_a_loop_ends(self) -> None:
        agent = _agent()
        agent._autopilot = MagicMock()
        observer = agent._review_loop_service._observer
        state = SimpleNamespace(task_id='UNA-1', status='stuck', round=2, max_rounds=5, reason='')
        with patch('kato_core_lib.data_layers.service.review_loop_adapters.log_review_loop_event'):
            observer(state, 'reviewing')
            agent._autopilot.review_loop_finished.assert_not_called()
            observer(state, 'finished')
        agent._autopilot.review_loop_finished.assert_called_once_with('UNA-1', 'stuck')


class DoneClearsTheParkTests(unittest.TestCase):

    def test_a_finished_done_leaves_nothing_to_approve(self) -> None:
        from kato_core_lib.data_layers.service.task_publish_service import TaskPublishService
        pending = {'UNA-1': ('task', 'prepared', 'execution'), 'UNA-2': ('t', 'p', 'e')}
        service = TaskPublishService(
            repository_service=MagicMock(), task_service=MagicMock(), task_state_service=MagicMock(),
            task_publisher=MagicMock(), workspace_manager=MagicMock(), pending_publish=pending,
        )
        service.push_task = MagicMock(return_value={'pushed_repositories': ['api']})
        service.create_pull_request_for_task = MagicMock(return_value={'created_pull_requests': [{}]})
        service._kick_lesson_extraction = MagicMock()
        service._is_plan_subtask = MagicMock(return_value=False)
        result = service.finish_task_planning_session('UNA-1', by_operator=True)
        self.assertTrue(result['finished'])
        self.assertEqual(list(pending), ['UNA-2'])

    def test_a_done_that_did_not_finish_keeps_the_park(self) -> None:
        from kato_core_lib.data_layers.service.task_publish_service import TaskPublishService
        pending = {'UNA-1': ('task', 'prepared', 'execution')}
        service = TaskPublishService(
            repository_service=MagicMock(), task_service=MagicMock(), task_state_service=MagicMock(),
            task_publisher=MagicMock(), workspace_manager=MagicMock(), pending_publish=pending,
        )
        failed = {'failed_repositories': [{'repository_id': 'api', 'error': 'denied'}]}
        service.push_task = MagicMock(return_value=failed)
        service.create_pull_request_for_task = MagicMock(return_value=failed)
        service._kick_lesson_extraction = MagicMock()
        result = service.finish_task_planning_session('UNA-1', by_operator=True)
        self.assertFalse(result['finished'])
        self.assertIn('UNA-1', pending)


if __name__ == '__main__':
    unittest.main()
