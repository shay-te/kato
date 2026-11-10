"""Deleting a plan's sub-task: its open pull requests declined first, then the
step out of the plan — and never the second without the first."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

from plan_queue_core_lib.plan_queue_core_lib.errors import PlanQueueError

from kato_core_lib.data_layers.service.plan_queue.step_deletion import PlanStepDeletion
from kato_core_lib.data_layers.service.repository_service import RepositoryService


def _step(**overrides):
    values = dict(id='api', task_id='UNA-3376', repositories=['ob-love-admin-backend', 'core-lib'])
    values.update(overrides)
    return SimpleNamespace(**values)


class StepDeletionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.step = _step()
        self.queue = Mock()
        self.queue.get.return_value = SimpleNamespace(step=lambda step_id: self.step if step_id == 'api' else None)
        self.queue.remove_step.return_value = 'the plan, without it'
        self.repos = Mock()
        self.repos.task_branch_name.return_value = 'UNA-3376'
        self.repos.get_repository.side_effect = lambda repo_id: SimpleNamespace(id=repo_id)
        self.repos.decline_open_pull_requests.side_effect = lambda repo, source_branch: (
            [{'id': '7', 'url': f'https://git/{repo.id}/pull/7'}] if repo.id == 'ob-love-admin-backend' else []
        )
        self.deletion = PlanStepDeletion(queue=self.queue, repository_service=self.repos)

    def test_its_pull_requests_are_declined_then_it_leaves_the_plan(self) -> None:
        outcome = self.deletion.delete('p1', 'api')
        self.assertEqual(outcome, {
            'task_id': 'UNA-3376', 'declined': ['https://git/ob-love-admin-backend/pull/7'],
            'problems': [], 'plan': 'the plan, without it',
        })
        self.assertEqual([call.kwargs['source_branch'] for call in self.repos.decline_open_pull_requests.call_args_list],
                         ['UNA-3376', 'UNA-3376'])
        self.queue.remove_step.assert_called_once_with('p1', 'api')

    def test_a_pull_request_that_could_not_be_declined_keeps_the_step(self) -> None:
        def refuse(repo, source_branch):
            if repo.id == 'core-lib':
                raise RuntimeError('403 Forbidden')
            return [{'id': '7'}]
        self.repos.decline_open_pull_requests.side_effect = refuse
        outcome = self.deletion.delete('p1', 'api')
        self.assertIsNone(outcome['plan'])
        self.assertEqual(outcome['declined'], ['7'])
        self.assertEqual(outcome['problems'], ['core-lib: its pull request could not be declined — 403 Forbidden'])
        self.queue.remove_step.assert_not_called()

    def test_a_step_with_no_task_has_nothing_to_decline(self) -> None:
        self.step = _step(task_id='')
        outcome = self.deletion.delete('p1', 'api')
        self.repos.decline_open_pull_requests.assert_not_called()
        self.assertEqual(outcome['plan'], 'the plan, without it')

    def test_an_unknown_step_gets_the_queues_own_refusal(self) -> None:
        self.queue.remove_step.side_effect = PlanQueueError('no step nope')
        with self.assertRaises(PlanQueueError):
            self.deletion.delete('p1', 'nope')

    def test_decline_pull_requests_is_public_for_a_cascading_delete(self) -> None:
        # The task a plan was split from being deleted tears every sub-task
        # down too, step by step, without going through remove_step for each
        # (the whole plan is stopped once instead) — it still needs this.
        declined, problems = self.deletion.decline_pull_requests(self.step)
        self.assertEqual(declined, ['https://git/ob-love-admin-backend/pull/7'])
        self.assertEqual(problems, [])


class DeclineOpenPullRequestsTests(unittest.TestCase):
    def test_only_the_open_ones_are_declined(self) -> None:
        service = RepositoryService.__new__(RepositoryService)
        service.pull_request_states = Mock(return_value=[
            {'id': '1', 'state': 'open'}, {'id': '2', 'state': 'merged'}, {'id': '3', 'state': 'OPEN'},
            {'id': '4', 'state': 'declined'}, {'id': '5'},
        ])
        access = MagicMock()
        service._pull_request_data_access = Mock(return_value=access)
        declined = service.decline_open_pull_requests(SimpleNamespace(id='api'), source_branch='UNA-1')
        self.assertEqual([pr['id'] for pr in declined], ['1', '3'])
        self.assertEqual([call.args[0] for call in access.decline_pull_request.call_args_list], ['1', '3'])
        service.pull_request_states.assert_called_once()

    def test_a_host_that_cannot_be_asked_raises_never_answers_none(self) -> None:
        service = RepositoryService.__new__(RepositoryService)
        service.pull_request_states = Mock(side_effect=RuntimeError('no token'))
        with self.assertRaises(RuntimeError):
            service.decline_open_pull_requests(SimpleNamespace(id='api'), source_branch='UNA-1')


if __name__ == '__main__':
    unittest.main()
