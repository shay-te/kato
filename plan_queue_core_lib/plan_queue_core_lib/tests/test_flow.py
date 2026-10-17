"""A plan from proposal to finish, the way a host drives it.

Plan: ``api`` and ``docs`` are independent; ``ui`` needs ``api``; ``release``
needs ``ui`` and ``docs``. Every step is released only once each step it
depends on is done AND merged.
"""

from __future__ import annotations

import tempfile
import unittest

from plan_queue_core_lib.plan_queue_core_lib.data.plan import PlanStatus, StepStatus
from plan_queue_core_lib.plan_queue_core_lib.ports import PullRequestFact, TaskPhase
from plan_queue_core_lib.plan_queue_core_lib.service import PlanQueueService
from plan_queue_core_lib.plan_queue_core_lib.store import PlanQueueStore
from plan_queue_core_lib.plan_queue_core_lib.tests.fakes import (
    FakeLauncher,
    FakeProbe,
    FakeProgress,
    FakeProposer,
    FakeWording,
    ManualClock,
    split_reply,
    step_spec,
    wait_until,
)

LATER = '2100-01-01T00:00:00Z'
SPLIT = split_reply([
    step_spec('api', repositories=['api']),
    step_spec('docs', repositories=['web']),
    step_spec('ui', depends_on=['api'], repositories=['web']),
    step_spec('release', depends_on=['ui', 'docs'], repositories=['api']),
])


class FlowTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix='plan-flow-')
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        self.clock = ManualClock(2_000_000_000.0)
        self.launcher = FakeLauncher()
        self.progress = FakeProgress()
        self.probe = FakeProbe()
        self.service = self._service()

    def _service(self, proposer=None) -> PlanQueueService:
        service = PlanQueueService(
            store=PlanQueueStore(self.root),
            proposer=proposer or FakeProposer(split_reply([step_spec('wrong')]), SPLIT),
            wording=FakeWording(), progress=self.progress, probe=self.probe, clock=self.clock,
        )
        service.bind_launcher(self.launcher)
        self.addCleanup(service.shutdown)
        return service

    def _start(self):
        plan = self.service.propose('SRC-1', plan_markdown='# Discounts', repository_ids=['api', 'web'])
        self.assertTrue(wait_until(lambda: self.service.get(plan.plan_id).status == PlanStatus.PROPOSED))
        # The operator didn't like the first split; the revision is the one above.
        self.service.revise(plan.plan_id, 'split the API from the page')
        self.assertTrue(wait_until(lambda: self.service.get(plan.plan_id).status == PlanStatus.PROPOSED))
        approved = self.service.approve(plan.plan_id, run=True)
        return {step.id: step.task_id for step in approved.steps}, plan.plan_id

    def _status(self, plan_id, step_id):
        return self.service.get(plan_id).step(step_id).status

    def _finish_step(self, plan_id, task_id, repo, *, merged=True):
        self.progress.set(task_id, TaskPhase.IN_PROGRESS)
        self.service.tick()
        self.progress.set(task_id, TaskPhase.IN_REVIEW)
        self.service.tick()
        self.progress.set(task_id, TaskPhase.DONE)
        if merged:
            self.probe.set(task_id, repo, PullRequestFact('merged', f'https://pr/{task_id}', LATER))
        self.service.tick()

    def test_a_to_z_with_tasks_created_at_approval(self) -> None:
        ids, plan_id = self._start()
        self.assertEqual(ids, {'api': 'T-101', 'docs': 'T-102', 'ui': 'T-103', 'release': 'T-104'})

        self.service.tick()  # api and docs are released; ui and release wait
        released = [task for _, task in self.launcher.released]
        self.assertEqual(released, ['T-101', 'T-102'])
        self.assertEqual(self._status(plan_id, 'ui'), StepStatus.BLOCKED)

        # api done but its PR still open: ui keeps waiting
        self.progress.set('T-101', TaskPhase.DONE)
        self.probe.set('T-101', 'api', PullRequestFact('open', 'https://pr/1', LATER))
        self.service.tick()
        self.assertEqual(self._status(plan_id, 'api'), StepStatus.AWAITING_MERGE)
        self.assertEqual(self._status(plan_id, 'ui'), StepStatus.BLOCKED)

        # merged: api is satisfied and ui is released on the next pass
        self.probe.set('T-101', 'api', PullRequestFact('merged', 'https://pr/1', LATER))
        self.clock.advance(60)
        self.service.tick()
        self.assertEqual(self._status(plan_id, 'api'), StepStatus.SATISFIED)
        self.assertEqual(self._status(plan_id, 'ui'), StepStatus.STARTING)

        self._finish_step(plan_id, 'T-103', 'web')
        self.assertEqual(self._status(plan_id, 'release'), StepStatus.BLOCKED)  # docs not done yet
        self._finish_step(plan_id, 'T-102', 'web')
        self.service.tick()
        self.assertEqual(self._status(plan_id, 'release'), StepStatus.STARTING)
        self._finish_step(plan_id, 'T-104', 'api')
        self.service.tick()
        self.assertEqual(self.service.get(plan_id).status, PlanStatus.FINISHED)

    def test_a_host_that_creates_tasks_at_release(self) -> None:
        self.launcher.creates_at_approval = False
        self.launcher.prefix = 'L'
        ids, plan_id = self._start()
        self.assertEqual(set(ids.values()), {''})  # nothing created up front
        self.service.tick()
        plan = self.service.get(plan_id)
        self.assertEqual((plan.step('api').task_id, plan.step('docs').task_id), ('L-101', 'L-102'))
        self.assertEqual(plan.step('ui').task_id, '')

    def test_a_declined_pull_request_holds_its_dependents_until_the_operator_acts(self) -> None:
        ids, plan_id = self._start()
        self.service.tick()
        self.progress.set('T-101', TaskPhase.DONE)
        self.probe.set('T-101', 'api', PullRequestFact('declined', 'https://pr/1', LATER))
        self.service.tick()
        self.assertEqual(self._status(plan_id, 'api'), StepStatus.FAILED)
        self.service.tick()
        self.assertEqual(self._status(plan_id, 'ui'), StepStatus.BLOCKED)
        # the operator opens a new PR that gets merged, and asks to look again
        self.probe.set('T-101', 'api', PullRequestFact('merged', 'https://pr/2', LATER))
        self.service.retry_step(plan_id, 'api')
        self.service.tick()
        self.assertEqual(self._status(plan_id, 'api'), StepStatus.SATISFIED)

    def test_a_task_removed_before_it_was_done_fails_its_step(self) -> None:
        ids, plan_id = self._start()
        self.service.tick()
        self.progress.set('T-101', TaskPhase.GONE)
        self.service.tick()
        step = self.service.get(plan_id).step('api')
        self.assertEqual((step.status, step.reason_code), (StepStatus.FAILED, 'task_gone'))

    def test_an_old_merge_on_a_reused_branch_never_releases_a_dependent(self) -> None:
        ids, plan_id = self._start()
        self.service.tick()
        self.progress.set('T-101', TaskPhase.DONE)
        self.probe.set('T-101', 'api', PullRequestFact('merged', 'https://old', '2001-01-01T00:00:00Z'))
        self.service.tick()
        self.assertEqual(self._status(plan_id, 'api'), StepStatus.AWAITING_MERGE)
        self.assertEqual(self._status(plan_id, 'ui'), StepStatus.BLOCKED)

    def test_a_restart_pauses_and_carries_on_after_run(self) -> None:
        ids, plan_id = self._start()
        self.service.tick()
        self.service.shutdown()
        restarted = self._service(proposer=FakeProposer(SPLIT))
        restarted.mark_interrupted('interrupted by a restart')
        self.assertEqual(restarted.get(plan_id).status, PlanStatus.PAUSED)
        self.progress.set('T-101', TaskPhase.DONE)
        self.probe.set('T-101', 'api', PullRequestFact('merged', 'https://pr/1', LATER))
        restarted.tick()  # tracked and merged even while paused...
        self.assertEqual(restarted.get(plan_id).step('api').status, StepStatus.SATISFIED)
        self.assertEqual(restarted.get(plan_id).step('ui').status, StepStatus.READY)
        restarted.run(plan_id)
        restarted.tick()  # ...but released only after Run
        self.assertEqual(restarted.get(plan_id).step('ui').status, StepStatus.STARTING)


if __name__ == '__main__':
    unittest.main()
