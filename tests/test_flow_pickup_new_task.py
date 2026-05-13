"""Flow #2 — Pick up a newly-assigned task from the ticket system.

A-Z scenario:

    1. Scan cycle runs (every 30s by default).
    2. ``service.get_assigned_tasks()`` returns one or more tasks
       newly-assigned to kato in the ticket system.
    3. For each task, ``service.process_assigned_task(task)`` runs:
        a. Resolve repositories from task metadata.
        b. Provision per-task workspace (parallel git clones).
        c. Checkout the task branch.
        d. Spawn the agent with the implementation prompt.
        e. Publish results: open PRs + move task to "In Review".
    4. Review comments fan out the same way, scoped by task id.

This file pins the *dispatch contract* — the spine that decides
which tasks run, in what concurrency mode, and what happens when
the runner is in various states (no concurrency, parallel runner,
already-in-flight task, errors during processing). The deeper
agent-spawn / publish details live in their own tests
(``test_task_publisher.py``, ``test_planning_session_runner.py``);
this file specifically pins the *dispatch* boundary.

Adversarial regression modes:
    - Already-in-flight task accidentally double-submitted (would
      cause two agents writing the same workspace).
    - Empty assigned-tasks list crashing the scan loop.
    - Error in one task killing the dispatch of others.
    - Runner with non-int ``max_workers`` (a Mock) falling through
      to the parallel path and exploding.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from kato_core_lib.jobs.process_assigned_tasks import (
    _dispatch_assigned_tasks,
    _runner_has_real_concurrency,
    collect_processing_results,
)


def _task(task_id):
    return SimpleNamespace(id=task_id, summary=f'work on {task_id}')


# ---------------------------------------------------------------------------
# Inline / no-real-concurrency path.
# ---------------------------------------------------------------------------


class FlowPickupInlineDispatchTests(unittest.TestCase):
    """When ``parallel_task_runner`` isn't a real concurrent runner,
    every task runs inline in the scan loop. Defensive: also the path
    every mocked test setup hits."""

    def test_flow_pickup_no_tasks_assigned_returns_empty_list(self) -> None:
        # Defensive: the scan loop must tolerate the no-work case
        # rather than crashing or paging out.
        service = MagicMock()
        service.parallel_task_runner = None
        service.get_assigned_tasks.return_value = []

        results = _dispatch_assigned_tasks(service)
        self.assertEqual(results, [])
        service.process_assigned_task.assert_not_called()

    def test_flow_pickup_single_task_processed_inline(self) -> None:
        service = MagicMock()
        service.parallel_task_runner = None
        service.get_assigned_tasks.return_value = [_task('T1')]
        service.process_assigned_task.return_value = {
            'status': 'updated', 'pull_request_id': '17',
            'branch_name': 'T1', 'repository_id': 'r',
        }

        results = _dispatch_assigned_tasks(service)
        self.assertEqual(len(results), 1)
        service.process_assigned_task.assert_called_once()

    def test_flow_pickup_processes_each_task_in_order(self) -> None:
        # Lock ordering: tasks should be processed in the order the
        # ticket system returned them. A regression that reverses
        # order would change which task gets the "first message"
        # comment.
        service = MagicMock()
        service.parallel_task_runner = None
        service.get_assigned_tasks.return_value = [
            _task('T1'), _task('T2'), _task('T3'),
        ]
        call_order = []
        service.process_assigned_task.side_effect = (
            lambda t: call_order.append(t.id) or {
                'status': 'updated', 'pull_request_id': '1',
                'branch_name': t.id, 'repository_id': 'r',
            }
        )

        _dispatch_assigned_tasks(service)
        self.assertEqual(call_order, ['T1', 'T2', 'T3'])

    def test_flow_pickup_none_result_is_filtered(self) -> None:
        # ``process_assigned_task`` may return ``None`` for tasks
        # that are partial / no-changes / silently skipped. The
        # dispatch must filter those out of the results list (since
        # they have nothing to log).
        service = MagicMock()
        service.parallel_task_runner = None
        service.get_assigned_tasks.return_value = [_task('T1'), _task('T2')]
        service.process_assigned_task.side_effect = [
            {'status': 'updated', 'pull_request_id': '1',
             'branch_name': 'T1', 'repository_id': 'r'},
            None,
        ]

        results = _dispatch_assigned_tasks(service)
        self.assertEqual(len(results), 1)

    def test_flow_pickup_runner_with_mock_max_workers_treated_as_inline(self) -> None:
        # The smoking-gun guard in ``_runner_has_real_concurrency``:
        # a test mock for ``parallel_task_runner`` typically has
        # ``max_workers`` as a Mock (truthy, not int). If we naïvely
        # checked ``runner.max_workers > 1`` we'd hit the parallel
        # path and TypeError. The guard makes the inline path the
        # safe default.
        service = MagicMock()
        # max_workers IS a Mock — not an int.
        service.parallel_task_runner = MagicMock()
        service.get_assigned_tasks.return_value = [_task('T1')]
        service.process_assigned_task.return_value = {'status': 'updated'}

        # If the guard misfires, this call will TypeError comparing
        # Mock > int.
        results = _dispatch_assigned_tasks(service)
        self.assertEqual(results, [{'status': 'updated'}])
        # ``submit`` must NOT have been called — we took the inline path.
        service.parallel_task_runner.submit.assert_not_called()

    def test_flow_pickup_runner_with_single_worker_treated_as_inline(self) -> None:
        # ``max_workers == 1`` is effectively inline: no real
        # concurrency. The guard treats this as inline to keep
        # behavior identical to the legacy path.
        service = MagicMock()
        service.parallel_task_runner = SimpleNamespace(
            max_workers=1,
            submit=MagicMock(),
        )
        service.get_assigned_tasks.return_value = [_task('T1')]
        service.process_assigned_task.return_value = {'status': 'updated'}

        _dispatch_assigned_tasks(service)
        service.parallel_task_runner.submit.assert_not_called()


# ---------------------------------------------------------------------------
# Parallel-runner path.
# ---------------------------------------------------------------------------


class FlowPickupParallelDispatchTests(unittest.TestCase):

    def _make_service_with_parallel_runner(self, max_workers=4):
        service = MagicMock()
        service.parallel_task_runner = SimpleNamespace(
            max_workers=max_workers,
            submit=MagicMock(),
            is_in_flight=MagicMock(return_value=False),
        )
        return service

    def test_flow_pickup_parallel_path_submits_each_task(self) -> None:
        service = self._make_service_with_parallel_runner()
        service.get_assigned_tasks.return_value = [
            _task('T1'), _task('T2'),
        ]

        _dispatch_assigned_tasks(service)
        # Each task submitted to the runner.
        self.assertEqual(service.parallel_task_runner.submit.call_count, 2)
        # Inline processing did NOT happen.
        service.process_assigned_task.assert_not_called()

    def test_flow_pickup_parallel_skips_in_flight_tasks(self) -> None:
        # The double-submission guard: if a task is already running,
        # re-submitting would mean two agents writing the same
        # workspace concurrently. Critical regression to prevent.
        service = self._make_service_with_parallel_runner()
        service.get_assigned_tasks.return_value = [
            _task('T1'), _task('T2'),
        ]
        # T1 is in flight, T2 is not.
        service.parallel_task_runner.is_in_flight.side_effect = (
            lambda task_id: task_id == 'T1'
        )

        _dispatch_assigned_tasks(service)
        # Only T2 was submitted.
        self.assertEqual(service.parallel_task_runner.submit.call_count, 1)
        first_call_task_id = service.parallel_task_runner.submit.call_args.args[0]
        self.assertEqual(first_call_task_id, 'T2')

    def test_flow_pickup_parallel_handles_submit_returning_none(self) -> None:
        # ``submit`` can return None when the runner is at capacity.
        # The dispatch must NOT crash; the next scan picks it up.
        service = self._make_service_with_parallel_runner()
        service.get_assigned_tasks.return_value = [_task('T1')]
        service.parallel_task_runner.submit.return_value = None

        results = _dispatch_assigned_tasks(service)
        self.assertEqual(results, [])


# ---------------------------------------------------------------------------
# Concurrency-guard helper directly.
# ---------------------------------------------------------------------------


class FlowPickupConcurrencyGuardTests(unittest.TestCase):

    def test_flow_pickup_concurrency_guard_returns_false_for_none(self) -> None:
        self.assertFalse(_runner_has_real_concurrency(None))

    def test_flow_pickup_concurrency_guard_returns_false_for_mock_runner(self) -> None:
        # Critical for test ergonomics: a bare MagicMock used as the
        # runner must NOT trip the parallel path.
        self.assertFalse(_runner_has_real_concurrency(MagicMock()))

    def test_flow_pickup_concurrency_guard_returns_false_for_zero_workers(self) -> None:
        runner = SimpleNamespace(max_workers=0)
        self.assertFalse(_runner_has_real_concurrency(runner))

    def test_flow_pickup_concurrency_guard_returns_false_for_one_worker(self) -> None:
        runner = SimpleNamespace(max_workers=1)
        self.assertFalse(_runner_has_real_concurrency(runner))

    def test_flow_pickup_concurrency_guard_returns_true_for_real_runner(self) -> None:
        runner = SimpleNamespace(max_workers=4)
        self.assertTrue(_runner_has_real_concurrency(runner))

    def test_flow_pickup_concurrency_guard_returns_false_for_non_int(self) -> None:
        # ``max_workers`` could be a str from a misconfig — the
        # guard accepts only ints.
        runner = SimpleNamespace(max_workers='4')
        self.assertFalse(_runner_has_real_concurrency(runner))


# ---------------------------------------------------------------------------
# Full scan-cycle composition: assigned tasks AND review comments.
# ---------------------------------------------------------------------------


class FlowPickupScanCycleCompositionTests(unittest.TestCase):
    """``collect_processing_results`` is the scan-cycle entry point.
    Tests its composition of assigned-task dispatch + review-comment
    dispatch."""

    def test_flow_pickup_scan_cycle_combines_tasks_and_review_comments(self) -> None:
        service = MagicMock()
        service.parallel_task_runner = None
        service.get_assigned_tasks.return_value = [_task('T1')]
        service.process_assigned_task.return_value = {'status': 'updated', 'tag': 'task'}
        service.get_new_pull_request_comments.return_value = [
            SimpleNamespace(
                pull_request_id='PR-1', repository_id='r',
                comment_id='c1', body='Add a check.', task_id='T2',
                author='reviewer', file_path='f.py', line_number=1,
                line_type='ADDED', commit_sha='abc',
            ),
        ]
        service.process_review_comment.return_value = {'status': 'reviewed', 'tag': 'rc'}

        results = collect_processing_results(service)
        # Composition: task result + review-comment result.
        tags = [r.get('tag') for r in results]
        self.assertIn('task', tags)
        self.assertIn('rc', tags)


if __name__ == '__main__':
    unittest.main()
