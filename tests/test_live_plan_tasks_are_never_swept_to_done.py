"""A live plan's tasks are never swept to ``done`` for lacking an assignee.

The done-sweep decided "is this task still wanted?" from the tracker's lists
alone: assigned to the bot, or in review. The plan queue holds a step UNASSIGNED
until its turn and hands a task back by unassigning it, and the task a plan was
split from is usually not the bot's any more — so all of them read as finished.
One was marked done while its plan was live: its tab greyed, and its card sat in
Waiting while the operator watched it in the working tabs.

The queue knows these tasks by their CODE (``live_task_codes``); the sweep asks.
"""

from __future__ import annotations

import logging
import unittest
from types import SimpleNamespace
from unittest import mock

from kato_core_lib.data_layers.service import task_cleanup_service as module
from kato_core_lib.data_layers.service.agent_service import AgentService


def _workspace(task_id, status='terminated'):
    return SimpleNamespace(task_id=task_id, status=status, updated_at_epoch=0.0)


class LivePlanTasksAreNotSweptToDoneTests(unittest.TestCase):
    def _sweep(self, *, plan_codes, workspaces, assigned=(), in_review=()):
        workspace_manager = mock.MagicMock()
        workspace_manager.list_workspaces.return_value = workspaces
        workspace_manager.get.side_effect = lambda task_id: next(
            (w for w in workspaces if w.task_id == task_id), None,
        )
        session_manager = mock.MagicMock()
        session_manager.list_records.return_value = []
        session_manager.get_session.return_value = None
        task_service = mock.MagicMock()
        task_service.get_assigned_tasks.return_value = [SimpleNamespace(id=t) for t in assigned]
        service = module.TaskCleanupService(
            task_service=task_service,
            session_manager=session_manager,
            workspace_manager=workspace_manager,
            live_plan_task_codes=plan_codes,
            logger=mock.MagicMock(spec=logging.Logger),
        )
        with mock.patch(
            'kato_core_lib.helpers.forgotten_tasks_store.forgotten_task_ids', return_value=set(),
        ):
            service._cleanup_done_planning_sessions({t.lower() for t in in_review})
        return service, workspace_manager

    @staticmethod
    def _marked_done(workspace_manager):
        return {call.args[0] for call in workspace_manager.update_status.call_args_list}

    def test_a_task_nobody_owns_is_still_marked_done(self) -> None:
        # The sweep keeps doing its job for ordinary finished tasks.
        _service, workspaces = self._sweep(
            plan_codes=set(), workspaces=[_workspace('UNA-9')],
        )
        self.assertEqual(self._marked_done(workspaces), {'UNA-9'})

    def test_a_plan_step_held_unassigned_is_not_marked_done(self) -> None:
        _service, workspaces = self._sweep(
            plan_codes={'una-3274'}, workspaces=[_workspace('UNA-3274'), _workspace('UNA-9')],
        )
        self.assertEqual(self._marked_done(workspaces), {'UNA-9'})

    def test_the_task_a_plan_was_split_from_is_not_marked_done(self) -> None:
        _service, workspaces = self._sweep(
            plan_codes={'una-3267'}, workspaces=[_workspace('UNA-3267')],
        )
        self.assertEqual(self._marked_done(workspaces), set())

    def test_the_match_is_on_the_code_whatever_its_case(self) -> None:
        # The tracker says UNA-3274, the record may say una-3274, the plan keeps either.
        _service, workspaces = self._sweep(
            plan_codes={'UNA-3274'}, workspaces=[_workspace('una-3274')],
        )
        self.assertEqual(self._marked_done(workspaces), set())

    def test_a_task_that_left_the_plan_is_swept_like_any_other(self) -> None:
        # Its step was counted done or cancelled, so the plan no longer owns the code.
        _service, workspaces = self._sweep(
            plan_codes={'una-3275'}, workspaces=[_workspace('UNA-3274')],
        )
        self.assertEqual(self._marked_done(workspaces), {'UNA-3274'})

    def test_a_task_the_bot_has_is_still_left_alone(self) -> None:
        _service, workspaces = self._sweep(
            plan_codes=set(), workspaces=[_workspace('UNA-1')], assigned=['UNA-1'],
        )
        self.assertEqual(self._marked_done(workspaces), set())

    def test_a_host_with_no_plan_queue_sweeps_as_before(self) -> None:
        _service, workspaces = self._sweep(plan_codes=None, workspaces=[_workspace('UNA-9')])
        self.assertEqual(self._marked_done(workspaces), {'UNA-9'})

    def test_the_agent_asks_the_queue_by_code(self) -> None:
        queue = mock.MagicMock()
        queue.live_task_codes.return_value = {'una-1'}
        agent = SimpleNamespace(_plan_queue_service=queue)
        self.assertEqual(AgentService._live_plan_task_codes.fget(agent), {'una-1'})

    def test_with_no_queue_the_agent_owns_no_codes(self) -> None:
        self.assertEqual(
            AgentService._live_plan_task_codes.fget(SimpleNamespace(_plan_queue_service=None)), set(),
        )


class UnreadablePlansTests(unittest.TestCase):
    def test_nothing_is_swept_when_the_plans_cannot_be_read(self) -> None:
        from kato_core_lib.helpers.late_binding import later

        class Host:
            @property
            def codes(self):
                raise RuntimeError('registry locked')

        workspaces = [_workspace('UNA-9')]
        workspace_manager = mock.MagicMock()
        workspace_manager.list_workspaces.return_value = workspaces
        task_service = mock.MagicMock()
        task_service.get_assigned_tasks.return_value = []
        service = module.TaskCleanupService(
            task_service=task_service,
            session_manager=mock.MagicMock(),
            workspace_manager=workspace_manager,
            live_plan_task_codes=later(Host(), 'codes'),
            logger=mock.MagicMock(spec=logging.Logger),
        )
        with mock.patch(
            'kato_core_lib.helpers.forgotten_tasks_store.forgotten_task_ids', return_value=set(),
        ):
            service._cleanup_done_planning_sessions(set())
        workspace_manager.update_status.assert_not_called()
        self.assertTrue(service.logger.warning.called)


if __name__ == '__main__':
    unittest.main()
