"""A ``kato:wait-planning`` task runs in Plan on EVERY spawn while the tag is on.

Reported: "when I have put the wait-planning tag in the task on youtrack kato
will spawn the claude in Edit automatically this let's claude auto edit stuff
even while I am discussing things with him".

The tag pinned ``plan`` on the ONE spawn that opened the hold. Every later
spawn — the operator's next message once that session went idle, a comment
run — went through the funnel with no mode and came back as acceptEdits. The
hold is now recorded per task and applied by the spawn funnel itself, and it is
released only when kato reads the ticket WITHOUT the tag — including a task
already In Progress, which the queue scan never reads.
"""
from __future__ import annotations

import ast
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from kato_core_lib.data_layers.data.fields import TaskTags
from kato_core_lib.data_layers.service.agent_service import AgentService
from kato_core_lib.data_layers.service.planning_session_runner import (
    PlanningSessionRunner,
    StreamingSessionDefaults,
)
from kato_core_lib.data_layers.service.task_service import TaskService
from kato_core_lib.data_layers.service.wait_planning_service import WaitPlanningService
from kato_core_lib.helpers.planning_hold_store import (
    held_permission_mode,
    set_planning_hold,
    task_is_planning_held,
)
from kato_core_lib.jobs.process_assigned_tasks import collect_processing_results
from tests.utils import build_task

REPO_ROOT = Path(__file__).resolve().parents[1]


class _HoldFileMixin:
    """Throwaway hold + mode files — never the operator's, never another test's."""

    def setUp(self) -> None:
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.holds_path = root / 'planning_holds.json'
        self.modes_path = root / 'plan_mode.json'
        patcher = patch.dict(os.environ, {
            'KATO_PLANNING_HOLD_PATH': str(self.holds_path),
            'KATO_PLAN_MODE_PATH': str(self.modes_path),
        })
        patcher.start()
        self.addCleanup(patcher.stop)


class PlanningHoldStoreTests(_HoldFileMixin, unittest.TestCase):
    def test_no_task_is_held_by_default(self) -> None:
        self.assertFalse(task_is_planning_held('UNA-1'))

    def test_a_hold_is_recorded_once_and_reports_the_change(self) -> None:
        self.assertTrue(set_planning_hold('UNA-1', True))
        self.assertTrue(task_is_planning_held('UNA-1'))
        self.assertFalse(set_planning_hold('UNA-1', True))

    def test_a_release_is_recorded_once_and_reports_the_change(self) -> None:
        set_planning_hold('UNA-1', True)
        self.assertTrue(set_planning_hold('UNA-1', False))
        self.assertFalse(task_is_planning_held('UNA-1'))
        self.assertFalse(set_planning_hold('UNA-1', False))

    def test_ids_match_whatever_their_case(self) -> None:
        set_planning_hold('UNA-1', True)
        self.assertTrue(task_is_planning_held('una-1'))
        self.assertFalse(set_planning_hold('una-1', True))
        self.assertEqual(json.loads(self.holds_path.read_text()), ['UNA-1'])
        self.assertTrue(set_planning_hold('una-1', False))
        self.assertFalse(task_is_planning_held('UNA-1'))

    def test_holds_are_on_disk_so_they_survive_a_restart(self) -> None:
        set_planning_hold('UNA-2', True)
        set_planning_hold('UNA-1', True)
        self.assertEqual(json.loads(self.holds_path.read_text()), ['UNA-1', 'UNA-2'])

    def test_a_hold_on_one_task_does_not_hold_another(self) -> None:
        set_planning_hold('UNA-1', True)
        self.assertFalse(task_is_planning_held('UNA-10'))

    def test_a_corrupt_file_holds_nothing_and_is_rewritten_cleanly(self) -> None:
        self.holds_path.write_text('{not json')
        self.assertFalse(task_is_planning_held('UNA-1'))
        self.assertTrue(set_planning_hold('UNA-1', True))
        self.assertEqual(json.loads(self.holds_path.read_text()), ['UNA-1'])

    def test_a_blank_id_is_never_held_or_written(self) -> None:
        self.assertFalse(set_planning_hold('  ', True))
        self.assertFalse(task_is_planning_held(''))
        self.assertFalse(self.holds_path.exists())

    def test_the_mode_rule(self) -> None:
        self.assertEqual(held_permission_mode('UNA-1', 'bypassPermissions'), 'bypassPermissions')
        set_planning_hold('UNA-1', True)
        for requested in ('', 'acceptEdits', 'bypassPermissions', 'explain', 'plan'):
            with self.subTest(requested=requested):
                self.assertEqual(held_permission_mode('UNA-1', requested), 'plan')


class EverySpawnOfAHeldTaskIsPlanTests(_HoldFileMixin, unittest.TestCase):
    """Asserted at ``start_session`` — the funnel every spawn path goes through."""

    def _runner(self):
        manager = MagicMock()
        runner = PlanningSessionRunner(
            session_manager=manager,
            defaults=StreamingSessionDefaults(permission_mode='acceptEdits'),
        )
        return runner, manager

    def _spawned_mode(self, **kwargs) -> str:
        runner, manager = self._runner()
        runner.start_session(
            task_id='UNA-1', task_summary='s', initial_prompt='p', cwd='/w', **kwargs,
        )
        return manager.start_session.call_args.kwargs['permission_mode']

    def test_an_unheld_task_keeps_the_configured_default(self) -> None:
        self.assertEqual(self._spawned_mode(), 'acceptEdits')

    def test_a_held_task_spawns_in_plan(self) -> None:
        set_planning_hold('UNA-1', True)
        self.assertEqual(self._spawned_mode(), 'plan')

    def test_the_hold_outranks_an_explicit_caller_mode(self) -> None:
        set_planning_hold('UNA-1', True)
        self.assertEqual(self._spawned_mode(permission_mode='bypassPermissions'), 'plan')

    def test_the_hold_outranks_the_operators_persisted_pick(self) -> None:
        self.modes_path.write_text(json.dumps({'UNA-1': 'bypassPermissions'}))
        set_planning_hold('UNA-1', True)
        self.assertEqual(self._spawned_mode(), 'plan')

    def test_releasing_the_hold_gives_the_default_back(self) -> None:
        set_planning_hold('UNA-1', True)
        set_planning_hold('UNA-1', False)
        self.assertEqual(self._spawned_mode(), 'acceptEdits')

    def test_a_chat_respawn_of_a_held_task_is_plan_not_an_explain_turn(self) -> None:
        # The report's path: the next message after the hold session exited.
        set_planning_hold('UNA-1', True)
        runner, _manager = self._runner()
        runner.start_session = MagicMock()
        runner.resume_session_for_chat(
            task_id='UNA-1', message='what do you think?', cwd='/w',
            permission_mode='explain',
        )
        kwargs = runner.start_session.call_args.kwargs
        self.assertEqual(kwargs['permission_mode'], 'plan')
        self.assertNotIn('ANSWER-ONLY', kwargs['initial_prompt'])


class _HoldServiceMixin(_HoldFileMixin):
    def _service(self, *, live_session=None, track=True):
        manager = MagicMock()
        manager.get_session.return_value = live_session
        service = WaitPlanningService(
            session_manager=manager,
            repository_service=Mock(**{'resolve_task_repositories.return_value': []}),
            task_state_service=Mock(),
            planning_session_runner=MagicMock(),
            track_planning_holds=track,
        )
        return service, manager


class WaitPlanningHoldTests(_HoldServiceMixin, unittest.TestCase):
    def test_the_queue_scan_holds_a_tagged_task(self) -> None:
        service, _manager = self._service()
        service.handle_task(build_task(tags=[TaskTags.WAIT_PLANNING]))
        self.assertTrue(task_is_planning_held('PROJ-1'))

    def test_the_queue_scan_releases_a_task_seen_without_the_tag(self) -> None:
        service, _manager = self._service()
        set_planning_hold('PROJ-1', True)
        self.assertIsNone(service.handle_task(build_task(tags=[])))
        self.assertFalse(task_is_planning_held('PROJ-1'))

    def test_wait_editing_is_not_a_planning_hold(self) -> None:
        service, _manager = self._service()
        set_planning_hold('PROJ-1', True)
        service.handle_task(build_task(tags=[TaskTags.WAIT_EDITING]))
        self.assertFalse(task_is_planning_held('PROJ-1'))

    def test_a_service_not_told_to_track_writes_no_hold(self) -> None:
        service, _manager = self._service(track=False)
        service.handle_task(build_task(tags=[TaskTags.WAIT_PLANNING]))
        self.assertFalse(task_is_planning_held('PROJ-1'))
        self.assertFalse(self.holds_path.exists())

    def test_the_started_task_sweep_holds_and_releases(self) -> None:
        service, _manager = self._service()
        set_planning_hold('PROJ-2', True)
        service.sync_planning_holds([
            build_task(task_id='PROJ-1', tags=[TaskTags.WAIT_PLANNING]),
            build_task(task_id='PROJ-2', tags=['unrelated']),
        ])
        self.assertTrue(task_is_planning_held('PROJ-1'))
        self.assertFalse(task_is_planning_held('PROJ-2'))

    def test_a_task_the_sweep_did_not_return_keeps_its_hold(self) -> None:
        service, _manager = self._service()
        set_planning_hold('PROJ-9', True)
        service.sync_planning_holds([])
        self.assertTrue(task_is_planning_held('PROJ-9'))

    def test_engaging_stops_a_live_session_that_can_edit(self) -> None:
        live = SimpleNamespace(is_alive=True, permission_mode='acceptEdits')
        service, manager = self._service(live_session=live)
        service.observe_planning_hold(build_task(tags=[TaskTags.WAIT_PLANNING]))
        manager.terminate_session.assert_called_once_with('PROJ-1', remove_record=False)

    def test_engaging_leaves_a_live_plan_session_alone(self) -> None:
        live = SimpleNamespace(is_alive=True, permission_mode='plan')
        service, manager = self._service(live_session=live)
        service.observe_planning_hold(build_task(tags=[TaskTags.WAIT_PLANNING]))
        manager.terminate_session.assert_not_called()

    def test_a_hold_already_in_place_stops_nothing_on_later_scans(self) -> None:
        live = SimpleNamespace(is_alive=True, permission_mode='acceptEdits')
        service, manager = self._service(live_session=live)
        set_planning_hold('PROJ-1', True)
        service.observe_planning_hold(build_task(tags=[TaskTags.WAIT_PLANNING]))
        manager.terminate_session.assert_not_called()

    def test_releasing_stops_nothing(self) -> None:
        live = SimpleNamespace(is_alive=True, permission_mode='plan')
        service, manager = self._service(live_session=live)
        set_planning_hold('PROJ-1', True)
        service.observe_planning_hold(build_task(tags=[]))
        manager.terminate_session.assert_not_called()

    def test_a_store_failure_is_logged_not_raised(self) -> None:
        service, manager = self._service()
        service.logger = MagicMock()
        with patch(
            'kato_core_lib.data_layers.service.wait_planning_service.set_planning_hold',
            side_effect=OSError('disk full'),
        ):
            service.observe_planning_hold(build_task(tags=[TaskTags.WAIT_PLANNING]))
        service.logger.exception.assert_called_once()
        manager.terminate_session.assert_not_called()

    def test_a_failed_stop_is_logged_not_raised(self) -> None:
        live = SimpleNamespace(is_alive=True, permission_mode='acceptEdits')
        service, manager = self._service(live_session=live)
        manager.terminate_session.side_effect = RuntimeError('gone')
        service.logger = MagicMock()
        service.observe_planning_hold(build_task(tags=[TaskTags.WAIT_PLANNING]))
        service.logger.exception.assert_called_once()
        self.assertTrue(task_is_planning_held('PROJ-1'))


def _agent_service(task_service, wait_planning_service):
    return AgentService(
        task_service=task_service,
        task_state_service=MagicMock(),
        implementation_service=MagicMock(),
        testing_service=MagicMock(),
        repository_service=MagicMock(),
        notification_service=MagicMock(),
        wait_planning_service=wait_planning_service,
    )


class AgentServiceHoldTests(_HoldServiceMixin, unittest.TestCase):
    def test_the_sweep_reads_started_tasks_and_holds_by_their_tags(self) -> None:
        planning, _manager = self._service()
        task_service = MagicMock()
        task_service.get_started_tasks.return_value = [
            build_task(task_id='UNA-7', tags=[TaskTags.WAIT_PLANNING]),
        ]
        _agent_service(task_service, planning).sync_planning_holds()
        self.assertTrue(task_is_planning_held('UNA-7'))

    def test_a_failed_tracker_read_keeps_every_hold(self) -> None:
        planning, _manager = self._service()
        set_planning_hold('UNA-7', True)
        task_service = MagicMock()
        task_service.get_started_tasks.side_effect = RuntimeError('rate limited')
        _agent_service(task_service, planning).sync_planning_holds()
        self.assertTrue(task_is_planning_held('UNA-7'))

    def test_without_tracking_the_tracker_is_not_read(self) -> None:
        planning, _manager = self._service(track=False)
        task_service = MagicMock()
        _agent_service(task_service, planning).sync_planning_holds()
        task_service.get_started_tasks.assert_not_called()

    def test_without_a_planning_service_nothing_happens(self) -> None:
        task_service = MagicMock()
        _agent_service(task_service, None).sync_planning_holds()
        task_service.get_started_tasks.assert_not_called()

    def _refresh(self, found_task, *, lookups_fail=False):
        planning, _manager = self._service()
        set_planning_hold('UNA-7', True)
        task_service = MagicMock()
        for queue in ('list_all_assigned_tasks', 'get_assigned_tasks', 'get_review_tasks'):
            fetch = getattr(task_service, queue)
            if lookups_fail:
                fetch.side_effect = RuntimeError('tracker down')
            else:
                fetch.return_value = [found_task] if found_task else []
        return _agent_service(task_service, planning).refresh_planning_hold('UNA-7')

    def test_refresh_releases_at_once_when_the_tag_is_gone(self) -> None:
        self.assertFalse(self._refresh(build_task(task_id='UNA-7', tags=[])))
        self.assertFalse(task_is_planning_held('UNA-7'))

    def test_refresh_keeps_the_hold_while_the_tag_is_on(self) -> None:
        self.assertTrue(
            self._refresh(build_task(task_id='UNA-7', tags=[TaskTags.WAIT_PLANNING])),
        )

    def test_refresh_keeps_the_hold_when_the_ticket_cannot_be_read(self) -> None:
        self.assertTrue(self._refresh(None, lookups_fail=True))

    def test_refresh_keeps_the_hold_when_the_ticket_is_not_found(self) -> None:
        self.assertTrue(self._refresh(None))

    # ----- leaving Plan without opening the tracker -----
    #
    # The operator: "i want to go out of planing mode without going to
    # youtrack." The tag has to come OFF the ticket: the hold is kato's
    # reading of it, and the next scan re-reads it, so clearing only the local
    # record would put the task back in Plan within a scan cycle.

    def test_release_takes_the_tag_off_the_ticket_and_drops_the_hold(self) -> None:
        planning, _manager = self._service()
        set_planning_hold('UNA-7', True)
        task_service = MagicMock()

        result = _agent_service(task_service, planning).release_planning_hold('UNA-7')

        task_service.remove_tag.assert_called_once_with('UNA-7', TaskTags.WAIT_PLANNING)
        self.assertTrue(result['ok'])
        self.assertFalse(task_is_planning_held('UNA-7'))

    def test_release_leaves_the_hold_when_the_ticket_cannot_be_written(self) -> None:
        # An unlocked picker over a spawn that still runs Plan is the failure
        # this whole mechanism exists to prevent — so the hold stands.
        planning, _manager = self._service()
        set_planning_hold('UNA-7', True)
        task_service = MagicMock()
        task_service.remove_tag.side_effect = RuntimeError('token rejected')

        result = _agent_service(task_service, planning).release_planning_hold('UNA-7')

        self.assertFalse(result['ok'])
        self.assertIn('token rejected', result['error'])
        self.assertTrue(task_is_planning_held('UNA-7'))

    def test_release_needs_a_task_id_before_it_writes_anything(self) -> None:
        planning, _manager = self._service()
        task_service = MagicMock()

        result = _agent_service(task_service, planning).release_planning_hold('')

        self.assertFalse(result['ok'])
        task_service.remove_tag.assert_not_called()


class TaskServiceStartedTasksTests(unittest.TestCase):
    def test_reads_in_progress_and_in_review(self) -> None:
        fake = SimpleNamespace(
            _configured_state_value={'progress': 'In Progress', 'review': 'In Review'}.get,
            get_assigned_tasks=MagicMock(return_value=['task']),
        )
        self.assertEqual(TaskService.get_started_tasks(fake), ['task'])
        fake.get_assigned_tasks.assert_called_once_with(
            assignee=None, states=['In Progress', 'In Review'],
        )

    def test_with_no_such_states_configured_it_reads_nothing(self) -> None:
        # An empty ``states`` would make get_assigned_tasks read the QUEUE.
        fake = SimpleNamespace(
            _configured_state_value={}.get,
            get_assigned_tasks=MagicMock(),
        )
        self.assertEqual(TaskService.get_started_tasks(fake), [])
        fake.get_assigned_tasks.assert_not_called()


class ScanCycleSyncsHoldsTests(unittest.TestCase):
    def test_holds_are_synced_before_any_task_is_dispatched(self) -> None:
        events: list[str] = []
        service = Mock()
        service.sync_planning_holds.side_effect = lambda: events.append('sync')
        service.get_assigned_tasks.side_effect = lambda: events.append('dispatch') or []
        service.comments.get_new_pull_request_comments.return_value = []
        service.parallel_task_runner = None
        collect_processing_results(service)
        self.assertEqual(events[:2], ['sync', 'dispatch'])

    def test_a_failed_sync_does_not_cost_the_cycle_its_tasks(self) -> None:
        service = Mock()
        service.sync_planning_holds.side_effect = RuntimeError('boom')
        service.get_assigned_tasks.return_value = ['task-1']
        service.process_assigned_task.return_value = {'id': 'task-1'}
        service.comments.get_new_pull_request_comments.return_value = []
        service.parallel_task_runner = None
        self.assertEqual(collect_processing_results(service), [{'id': 'task-1'}])


class ProductionWiringTests(unittest.TestCase):
    def test_the_app_turns_hold_tracking_on(self) -> None:
        # Tracking is off by default so unit tests cannot leak holds into each
        # other — which makes a wiring slip a silent loss of the whole feature.
        source = (REPO_ROOT / 'kato_core_lib' / 'kato_core_lib.py').read_text()
        calls = [
            node for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call)
            and getattr(node.func, 'id', '') == 'WaitPlanningService'
        ]
        self.assertEqual(len(calls), 1)
        keywords = {kw.arg: kw.value for kw in calls[0].keywords}
        self.assertIn('track_planning_holds', keywords)
        self.assertIs(ast.literal_eval(keywords['track_planning_holds']), True)


if __name__ == '__main__':
    unittest.main()
