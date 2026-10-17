"""Every call to the host's launcher ends as an outcome, never a stray exception."""

from __future__ import annotations

import logging
import unittest

from plan_queue_core_lib.plan_queue_core_lib.data.plan import FailReason, Plan, Step
from plan_queue_core_lib.plan_queue_core_lib.launching import (
    build_request,
    prepare_task,
    release_task,
    count_done,
    withdraw_task,
)
from plan_queue_core_lib.plan_queue_core_lib.ports import LaunchRefused
from plan_queue_core_lib.plan_queue_core_lib.tests.fakes import FakeLauncher


def _plan() -> Plan:
    plan = Plan.new('SRC-1', plan_markdown='x', repository_ids=['api'], now=0, title='Plan')
    plan.start_mode, plan.step_model, plan.effort = 'implement', 'sonnet', 'high'
    plan.steps = [Step(id='a', title='A', brief='do a', repositories=['api'])]
    return plan


class LaunchingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.launcher = FakeLauncher()
        plan = _plan()
        self.request = build_request(plan, plan.steps[0])

    def test_the_request_carries_everything_the_host_needs(self) -> None:
        request = self.request
        self.assertEqual((request.index, request.count, request.title), (1, 1, 'A'))
        self.assertEqual(request.repositories, ('api',))
        self.assertEqual((request.source_task_id, request.plan_title), ('SRC-1', 'Plan'))
        self.assertEqual((request.start_mode, request.model, request.effort), ('implement', 'sonnet', 'high'))
        self.assertIn('do a', request.description)
        self.assertEqual(request.marker, f'plan-step:{request.plan_id}/a')

    def test_prepare(self) -> None:
        self.assertEqual(prepare_task(self.launcher, self.request).task_id, 'T-101')
        self.launcher.prepare_errors['a'] = RuntimeError('tracker said no')
        failed = prepare_task(self.launcher, self.request)
        self.assertEqual((failed.ok, failed.fail_reason, failed.message),
                         (False, FailReason.CREATE_FAILED, 'tracker said no'))
        self.launcher.prepare_returns_empty.add('a')
        self.assertEqual(prepare_task(self.launcher, self.request).message, 'the task was not created')

    def test_release(self) -> None:
        self.assertEqual(release_task(self.launcher, self.request, 'T-5').task_id, 'T-5')
        self.launcher.release_errors['a'] = LaunchRefused('repo not approved')
        refused = release_task(self.launcher, self.request, 'T-5')
        self.assertEqual((refused.fail_reason, refused.message), (FailReason.LAUNCH_REFUSED, 'repo not approved'))
        self.launcher.release_errors['a'] = ValueError()
        failed = release_task(self.launcher, self.request, 'T-5')
        self.assertEqual((failed.fail_reason, failed.message), (FailReason.LAUNCH_FAILED, 'ValueError'))
        self.assertEqual(release_task(self.launcher, self.request, '').message, 'the task was not started')

    def test_withdraw_is_best_effort(self) -> None:
        logger = logging.getLogger('test-withdraw')
        withdraw_task(None, 'T-1', 'why', logger)
        withdraw_task(self.launcher, '', 'why', logger)
        self.assertEqual(self.launcher.withdrawn, [])
        withdraw_task(self.launcher, 'T-1', 'why', logger)
        self.assertEqual(self.launcher.withdrawn, [('T-1', 'why')])

        class Broken(FakeLauncher):
            def withdraw(self, task_id, reason):
                raise RuntimeError('down')

        with self.assertLogs('test-withdraw', level='WARNING'):
            withdraw_task(Broken(), 'T-1', 'why', logger)

    def test_counting_done_marks_the_task_where_everyone_sees_it(self) -> None:
        count_done(self.launcher, 'T-1')
        self.assertEqual(self.launcher.counted, ['T-1'])
        count_done(self.launcher, '')      # no task anywhere: nothing to mark
        count_done(None, '')
        self.assertEqual(self.launcher.counted, ['T-1'])
        with self.assertRaises(RuntimeError):
            count_done(None, 'T-1')         # a task nobody here can reach: never done only here


if __name__ == '__main__':
    unittest.main()
