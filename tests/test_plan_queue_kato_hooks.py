"""Where the plan queue's tracker mode reaches into kato's existing services.

- the pickup gate: a plan's sub-task assigned before its turn is not started;
- repository resolution: a sub-task's footer names where it works;
- the tracker and its workflow as the plan queue sees them.
"""

from __future__ import annotations

import types
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from omegaconf import OmegaConf
from plan_queue_core_lib.plan_queue_core_lib.ports import LaunchRequest

from kato_core_lib.data_layers.data_access.task_data_access import TaskDataAccess
from kato_core_lib.data_layers.service.agent_service import AgentService
from kato_core_lib.data_layers.service.plan_queue.step_footer import subtask_description
from kato_core_lib.data_layers.service.repository_inventory_service import RepositoryInventoryService
from kato_core_lib.data_layers.service.task_preflight_service import TaskPreflightService
from kato_core_lib.data_layers.service.task_service import TaskService
from kato_core_lib.data_layers.service.task_state_service import TaskStateService
from tests.utils import build_task

PLAN_ID = 'd' * 32


def footer(repositories=('demo-shop-be',)) -> str:
    request = LaunchRequest(
        plan_id=PLAN_ID, step_id='ui', index=2, count=2, title='UI', description='Do the UI.',
        repositories=(), source_task_id='PROJ-1', plan_title='T', waits_for=('PROJ-11',),
    )
    return subtask_description(request, list(repositories))


class PickupGateTests(unittest.TestCase):
    def _preflight(self, gate):
        return TaskPreflightService(
            task_model_access_validator=Mock(), task_service=Mock(), repository_service=Mock(),
            task_branch_push_validator=Mock(), task_branch_publishability_validator=Mock(),
            plan_step_gate=gate,
        )

    def test_a_sub_task_waiting_its_turn_is_skipped_and_said_once(self) -> None:
        service = self._preflight(lambda task: 'it waits for PROJ-11 to be merged and done')
        task = build_task(task_id='PROJ-12')
        with self.assertLogs(service.logger, 'INFO') as logs:
            first = service.skip_if_waiting_its_turn(task)
            second = service.skip_if_waiting_its_turn(task)
        self.assertEqual(first, second)
        self.assertEqual(first['id'], 'PROJ-12')
        self.assertEqual(len(logs.output), 1)
        self.assertIn('not starting task PROJ-12 yet: it waits for PROJ-11', logs.output[0])

    def test_its_turn_or_no_plan_lets_the_pickup_go_on(self) -> None:
        self.assertIsNone(self._preflight(lambda task: '').skip_if_waiting_its_turn(build_task()))
        self.assertIsNone(self._preflight(None).skip_if_waiting_its_turn(build_task()))

    def test_a_gate_that_breaks_holds_the_task_rather_than_start_it_out_of_order(self) -> None:
        service = self._preflight(Mock(side_effect=RuntimeError('tracker down')))
        with self.assertLogs(service.logger, 'INFO') as logs:
            result = service.skip_if_waiting_its_turn(build_task(task_id='PROJ-12'))
        self.assertEqual(result['id'], 'PROJ-12')
        self.assertIn('its plan could not be checked: tracker down', logs.output[0])

    def _agent(self, wait_reason):
        agent = AgentService.__new__(AgentService)
        agent._task_preflight_service = self._preflight(lambda task: wait_reason)
        agent._task_preflight_service.prepare_task_execution_context = Mock(return_value=None)
        agent._task_repository_service = Mock()
        agent._triage_service = Mock(handle_task=Mock(return_value=None))
        agent._wait_planning_service = Mock(handle_task=Mock(return_value={'id': 'PROJ-12', 'planning': True}))
        return agent

    def test_no_pickup_path_jumps_the_order_not_even_a_tagged_one(self) -> None:
        # Triage and wait-planning clone and start a chat too: a sub-task tagged
        # kato:wait-planning before its turn must still wait.
        agent = self._agent('it waits for PROJ-11 to be merged and done')
        task = build_task(task_id='PROJ-12', tags=['kato:wait-planning'])
        result = agent.process_assigned_task(task)
        self.assertEqual(result['id'], 'PROJ-12')
        agent._triage_service.handle_task.assert_not_called()
        agent._wait_planning_service.handle_task.assert_not_called()
        agent._task_preflight_service.prepare_task_execution_context.assert_not_called()

    def test_at_its_turn_the_pickup_goes_on_as_usual(self) -> None:
        agent = self._agent('')
        task = build_task(task_id='PROJ-12', tags=['kato:wait-planning'])
        self.assertEqual(agent.process_assigned_task(task), {'id': 'PROJ-12', 'planning': True})
        agent._triage_service.handle_task.assert_called_once_with(task)

    def test_the_agent_service_asks_its_plan_queue(self) -> None:
        agent = AgentService.__new__(AgentService)
        self.assertEqual(agent._plan_step_wait_reason(build_task(description=footer())), '')  # no queue yet
        agent._plan_queue_service = Mock(waiting_for=Mock(return_value=['PROJ-11']))
        reason = agent._plan_step_wait_reason(build_task(task_id='PROJ-12', description=footer()))
        self.assertEqual(reason, 'it waits for PROJ-11 to be merged and done')
        self.assertEqual(agent._plan_step_wait_reason(build_task(description='a plain ticket')), '')


class FooterRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.backend = types.SimpleNamespace(id='backend', display_name='Backend', local_path='/w/backend',
                                             repo_slug='demo-shop-be', aliases=[])
        self.frontend = types.SimpleNamespace(id='frontend', display_name='Frontend', local_path='/w/frontend',
                                              repo_slug='demo-shop-fe', aliases=[])
        self.service = RepositoryInventoryService([self.backend, self.frontend])

    def test_the_footer_names_where_a_sub_task_works_before_any_tag(self) -> None:
        task = build_task(description=footer(('demo-shop-be', 'demo-shop-be')), tags=['kato:repo:frontend'])
        self.assertEqual([repo.id for repo in self.service.resolve_task_repositories(task)], ['backend'])

    def test_a_repository_this_kato_does_not_have_is_named(self) -> None:
        task = build_task(task_id='PROJ-12', description=footer(('demo-shop-be', 'mobile-app')))
        with self.assertRaises(ValueError) as caught:
            self.service.resolve_task_repositories(task)
        self.assertIn('task PROJ-12 works in mobile-app, which this kato has no repository for', str(caught.exception))

    def test_a_repository_added_from_kato_joins_the_footers(self) -> None:
        # "+ Add repository" on a plan sub-task: the tag was saved and nothing was
        # cloned, because the footer won outright — UNA-3272 + event-core-lib.
        self.service.attach_kato_task_tags(lambda task_id: ['kato:repo:frontend'])
        task = build_task(description=footer(('demo-shop-be',)))
        self.assertEqual(
            [repo.id for repo in self.service.resolve_task_repositories(task)],
            ['backend', 'frontend'],
        )

    def test_the_footer_keeps_its_place_and_nothing_is_listed_twice(self) -> None:
        self.service.attach_kato_task_tags(
            lambda task_id: ['kato:repo:backend', 'kato:repo:frontend', 'kato:repo:FRONTEND'],
        )
        task = build_task(description=footer(('demo-shop-be',)))
        self.assertEqual(
            [repo.id for repo in self.service.resolve_task_repositories(task)],
            ['backend', 'frontend'],
        )

    def test_a_repository_added_from_kato_that_matches_nothing_is_left_out_not_fatal(self) -> None:
        self.service.attach_kato_task_tags(lambda task_id: ['kato:repo:no-such-repo'])
        task = build_task(description=footer(('demo-shop-be',)))
        with self.assertLogs(self.service.logger, 'WARNING') as logs:
            repos = self.service.resolve_task_repositories(task)
        self.assertEqual([repo.id for repo in repos], ['backend'])
        self.assertIn('no-such-repo', logs.output[0])

    def test_tags_on_the_ticket_still_rank_below_the_footer_with_kato_tags_present(self) -> None:
        # The footer outranks what the TRACKER says (another kato may not see it or
        # may name it differently); only this kato's own recorded tags add.
        self.service.attach_kato_task_tags(lambda task_id: [])
        task = build_task(description=footer(('demo-shop-be',)), tags=['kato:repo:frontend'])
        self.assertEqual([repo.id for repo in self.service.resolve_task_repositories(task)], ['backend'])

    def test_a_footer_for_every_repository_falls_back_to_the_usual_rules(self) -> None:
        task = build_task(description=footer(()), tags=['kato:repo:frontend'])
        self.assertEqual([repo.id for repo in self.service.resolve_task_repositories(task)], ['frontend'])


class TrackerAccessTests(unittest.TestCase):
    def test_the_tracker_is_offered_only_when_it_can_hold_sub_tasks(self) -> None:
        able = SimpleNamespace(supports_subtasks=True)
        data_access = TaskDataAccess(OmegaConf.create({}), able)
        self.assertIs(data_access.subtask_tracker, able)
        self.assertIsNone(TaskDataAccess(OmegaConf.create({}), SimpleNamespace()).subtask_tracker)
        self.assertIsNone(TaskDataAccess(OmegaConf.create({}), Mock()).subtask_tracker)  # a mock is not "True"
        service = TaskService(OmegaConf.create({'assignee': 'me'}), data_access)
        self.assertIs(service.subtask_tracker(), able)

    def test_the_workflow_the_queue_reads_states_against(self) -> None:
        config = OmegaConf.create({
            'progress_state_field': 'State', 'progress_state': 'In Progress',
            'review_state': 'To Verify', 'done_state': 'Fixed',
        })
        names = TaskStateService(config, Mock()).state_names()
        self.assertEqual(names, {'field': 'State', 'progress': 'In Progress', 'review': 'To Verify', 'done': 'Fixed'})


if __name__ == '__main__':
    unittest.main()
