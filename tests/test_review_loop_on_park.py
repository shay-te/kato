"""``kato:review-loop``: a finished task starts its own review loop once it parks.

No tag, no loop. The first half drives the park step directly; the second drives
the real ``process_assigned_task``, because "wait-planning never gets a loop" and
"auto-push never gets a loop" are true only of the order the steps run in, and a
copy of that order here would keep passing after the real one changed.

The parked task reaching a real loop over a real diff is pinned in
``webserver/tests/test_review_loop_routes.py``.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, PropertyMock, patch

from kato_core_lib.data_layers.data.fields import StatusFields, TaskTags
from kato_core_lib.data_layers.data.task import Task
from kato_core_lib.data_layers.service.agent_service import AgentService
from kato_core_lib.helpers.task_context_utils import PreparedTaskContext

AWAITING = 'awaiting_push_approval'


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


def _prepared() -> PreparedTaskContext:
    return PreparedTaskContext(repositories=[], repository_branches={}, branch_name='T-1')


def _task(*tags: str) -> Task:
    return Task(id='T-1', tags=list(tags))


class ParkStepTests(unittest.TestCase):

    def _park(self, agent: AgentService, *tags: str) -> dict:
        return agent._pause_for_push_approval(_task(*tags), _prepared(), {'success': True})

    def test_the_bare_tag_starts_the_loop_with_the_default_rounds(self) -> None:
        agent = _agent()
        self._park(agent, TaskTags.REVIEW_LOOP)
        agent.parked_task_listener.assert_called_once_with('T-1', 5)

    def test_the_tag_picks_the_rounds(self) -> None:
        agent = _agent()
        self._park(agent, 'kato:review-loop:10')
        agent.parked_task_listener.assert_called_once_with('T-1', 10)

    def test_no_tag_no_loop(self) -> None:
        agent = _agent()
        self._park(agent, 'kato:repo:api', 'kato:model:opus')
        agent.parked_task_listener.assert_not_called()

    def test_a_malformed_tag_starts_nothing_and_says_so(self) -> None:
        for tag in ('kato:review-loop:0', 'kato:review-loop:99', 'kato:review-loop:abc'):
            with self.subTest(tag=tag):
                agent = _agent()
                with self.assertLogs(agent.logger, 'WARNING') as logs:
                    self._park(agent, tag)
                agent.parked_task_listener.assert_not_called()
                self.assertEqual(len(logs.output), 1)
                self.assertIn(tag, logs.output[0])

    def test_a_bad_tag_beside_a_good_one_does_not_cancel_it(self) -> None:
        agent = _agent()
        with self.assertLogs(agent.logger, 'WARNING'):
            self._park(agent, 'kato:review-loop:abc', 'kato:review-loop:8')
        agent.parked_task_listener.assert_called_once_with('T-1', 8)

    def test_two_tags_take_the_larger_whatever_their_order(self) -> None:
        for tags in (('kato:review-loop', 'kato:review-loop:12'),
                     ('kato:review-loop:12', 'kato:review-loop')):
            with self.subTest(tags=tags):
                agent = _agent()
                self._park(agent, *tags)
                agent.parked_task_listener.assert_called_once_with('T-1', 12)

    def test_the_loop_starts_after_the_task_is_parked(self) -> None:
        # The fix turns need the workspace in review, and the operator's Push
        # needs the parked task, so nothing may be left to do once this runs.
        agent = _agent()
        seen = {}

        def record(task_id: str, rounds: int) -> None:
            seen['parked'] = task_id in agent._pending_publish
            seen['comments'] = agent._task_service.add_comment.call_count
            seen['status_calls'] = agent._workspace_manager.update_status.call_count

        agent.parked_task_listener.side_effect = record
        self._park(agent, TaskTags.REVIEW_LOOP)
        self.assertEqual(seen, {'parked': True, 'comments': 1, 'status_calls': 1})

    def test_a_loop_that_cannot_start_leaves_the_task_parked(self) -> None:
        agent = _agent()
        agent.parked_task_listener.side_effect = RuntimeError('the chat is in Plan mode')
        with self.assertLogs(agent.logger, 'ERROR') as logs:
            result = self._park(agent, TaskTags.REVIEW_LOOP)
        self.assertEqual(result[StatusFields.STATUS], AWAITING)
        self.assertIn('T-1', agent._pending_publish)
        self.assertIn('could not start the review loop for task T-1', logs.output[0])

    def test_no_webserver_leaves_the_task_parked_and_says_so(self) -> None:
        agent = _agent()
        agent.parked_task_listener = None
        with self.assertLogs(agent.logger, 'WARNING') as logs:
            result = self._park(agent, TaskTags.REVIEW_LOOP)
        self.assertEqual(result[StatusFields.STATUS], AWAITING)
        self.assertIn('no webserver is running', logs.output[0])


class PickupFlowTests(unittest.TestCase):
    """Through ``process_assigned_task``, stubbing only the agent's own work."""

    def _flow_agent(self, wait_planning=None) -> AgentService:
        preflight = MagicMock()
        preflight.skip_if_waiting_its_turn.return_value = None
        preflight.prepare_task_execution_context.return_value = _prepared()
        planning = MagicMock()
        planning.handle_task.return_value = wait_planning
        agent = _agent(task_preflight_service=preflight, wait_planning_service=planning)
        agent._task_repository_service = MagicMock()
        agent._start_task_processing = MagicMock(return_value=True)
        agent._run_task_implementation = MagicMock(return_value={'success': True})
        agent._run_task_testing_validation = MagicMock(
            side_effect=lambda task, prepared, execution: (True, None, execution),
        )
        return agent

    def _run(self, agent: AgentService, task: Task, *, auto_push: bool = False):
        publisher = MagicMock()
        publisher.publish_execution.return_value = {StatusFields.STATUS: 'published'}
        with patch('kato_core_lib.data_layers.service.agent_service.auto_push_enabled',
                   return_value=auto_push), \
                patch.object(AgentService, 'publish', new_callable=PropertyMock,
                             return_value=publisher):
            return agent.process_assigned_task(task), publisher

    def test_a_tagged_task_is_implemented_tested_parked_then_reviewed(self) -> None:
        agent = self._flow_agent()
        result, publisher = self._run(agent, _task('kato:review-loop:3'))
        self.assertEqual(result[StatusFields.STATUS], AWAITING)
        agent.parked_task_listener.assert_called_once_with('T-1', 3)
        publisher.publish_execution.assert_not_called()

    def test_an_untagged_task_only_parks(self) -> None:
        agent = self._flow_agent()
        result, _publisher = self._run(agent, _task())
        self.assertEqual(result[StatusFields.STATUS], AWAITING)
        agent.parked_task_listener.assert_not_called()

    def test_wait_planning_wins_over_the_tag(self) -> None:
        agent = self._flow_agent(wait_planning={'id': 'T-1', 'planning': True})
        task = _task(TaskTags.WAIT_PLANNING, TaskTags.REVIEW_LOOP)
        result, _publisher = self._run(agent, task)
        self.assertEqual(result, {'id': 'T-1', 'planning': True})
        agent.parked_task_listener.assert_not_called()
        agent._run_task_implementation.assert_not_called()

    def test_auto_push_publishes_and_starts_no_loop(self) -> None:
        # Documented, not ideal: with the switch on the task never parks. A loop
        # started now would review a change that is already on the remote.
        agent = self._flow_agent()
        result, publisher = self._run(agent, _task(TaskTags.REVIEW_LOOP), auto_push=True)
        self.assertEqual(result[StatusFields.STATUS], 'published')
        publisher.publish_execution.assert_called_once()
        agent.parked_task_listener.assert_not_called()

    def test_the_stricter_push_tag_still_parks_and_reviews_under_auto_push(self) -> None:
        agent = self._flow_agent()
        task = _task(TaskTags.WAIT_BEFORE_GIT_PUSH, TaskTags.REVIEW_LOOP)
        result, publisher = self._run(agent, task, auto_push=True)
        self.assertEqual(result[StatusFields.STATUS], AWAITING)
        agent.parked_task_listener.assert_called_once_with('T-1', 5)
        publisher.publish_execution.assert_not_called()


if __name__ == '__main__':
    unittest.main()
