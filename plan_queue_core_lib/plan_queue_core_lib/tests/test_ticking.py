"""The background pass: track, merge, unblock, release, finish."""

from __future__ import annotations

import tempfile
import unittest
from unittest import mock

from plan_queue_core_lib.plan_queue_core_lib.data.plan import (
    Plan,
    PlanStatus,
    RepoMergeState,
    Step,
    StepStatus,
)
from plan_queue_core_lib.plan_queue_core_lib.merge import PROBE_BACKOFF_SECONDS
from plan_queue_core_lib.plan_queue_core_lib.ports import LaunchRefused, PullRequestFact, TaskPhase
from plan_queue_core_lib.plan_queue_core_lib.registry import PlanRegistry
from plan_queue_core_lib.plan_queue_core_lib.store import PlanQueueStore
from plan_queue_core_lib.plan_queue_core_lib.ticking import (
    MAX_PROBES_PER_TICK,
    MERGE_WAIT_REASON,
    NO_PR_REASON,
    PlanTicker,
)
from plan_queue_core_lib.plan_queue_core_lib.tests.fakes import (
    FakeLauncher,
    FakeProbe,
    FakeProgress,
    ManualClock,
)

LATER = '2100-01-01T00:00:00Z'  # always after a release in these tests


class TickerTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix='plan-tick-')
        self.addCleanup(tmp.cleanup)
        self.clock = ManualClock(1_000.0)
        self.registry = PlanRegistry(PlanQueueStore(tmp.name), clock=self.clock)
        self.progress = FakeProgress()
        self.probe = FakeProbe()
        self.launcher = FakeLauncher()
        self.events: list[str] = []
        self.ticker = PlanTicker(
            self.registry, progress=self.progress, probe=self.probe,
            launcher=lambda plan: self.launcher, clock=self.clock,
            observer=lambda plan, event: self.events.append(event),
        )

    def _plan(self, *steps: Step, status: PlanStatus = PlanStatus.RUNNING) -> Plan:
        plan = Plan.new('SRC-1', plan_markdown='x', repository_ids=['api', 'web'], now=1.0)
        plan.status = status
        plan.steps = list(steps)
        return self.registry.add(plan)

    def _step(self, plan: Plan, step_id: str) -> Step:
        return self.registry.get(plan.plan_id).step(step_id)

    # ----- track -----

    def test_a_released_step_follows_its_task(self) -> None:
        plan = self._plan(Step(id='a', title='A', brief='b', status=StepStatus.STARTING, task_id='T-1',
                               released_at=999.0, repositories=['api']))
        self.progress.set('T-1', TaskPhase.IN_PROGRESS)
        self.ticker.tick()
        self.assertEqual(self._step(plan, 'a').status, StepStatus.ACTIVE)
        self.progress.set('T-1', TaskPhase.DONE)
        self.ticker.tick()
        step = self._step(plan, 'a')
        # DONE -> waiting for the merge, probed in the same tick (nothing found yet)
        self.assertEqual(step.status, StepStatus.AWAITING_MERGE)
        self.assertEqual(step.reason, NO_PR_REASON)
        self.assertEqual(self.probe.calls, [('T-1', 'api')])
        self.assertIn('T-1: awaiting_merge', self.events)

    def test_unknown_tasks_and_a_failing_read_change_nothing(self) -> None:
        plan = self._plan(Step(id='a', title='A', brief='b', status=StepStatus.ACTIVE, task_id='T-1'))
        self.ticker.tick()
        self.assertEqual(self._step(plan, 'a').status, StepStatus.ACTIVE)
        self.progress.error = RuntimeError('tracker down')
        with self.assertLogs('PlanTicker', level='ERROR'):
            self.ticker.tick()
        self.assertEqual(self._step(plan, 'a').status, StepStatus.ACTIVE)

    def test_a_step_that_moved_meanwhile_is_not_overwritten(self) -> None:
        plan = self._plan(Step(id='a', title='A', brief='b', status=StepStatus.ACTIVE, task_id='T-1'))
        self.progress.set('T-1', TaskPhase.IN_REVIEW)
        original = self.progress.observe

        def observe_then_operator_acts(task_ids):
            result = original(task_ids)
            self.registry.mutate(plan.plan_id, lambda live: setattr(live.step('a'), 'status',
                                                                    StepStatus.CANCELLED))
            return result

        self.progress.observe = observe_then_operator_acts
        self.ticker.tick()
        self.assertEqual(self._step(plan, 'a').status, StepStatus.CANCELLED)

    # ----- merge -----

    def _awaiting(self, plan_status=PlanStatus.RUNNING, **step_changes) -> Plan:
        # Released in 2023, so a pull request from 2000 is older than the step.
        step = Step(id='a', title='A', brief='b', status=StepStatus.AWAITING_MERGE, task_id='T-1',
                    released_at=1_700_000_000.0, repositories=['api', 'web'])
        for key, value in step_changes.items():
            setattr(step, key, value)
        return self._plan(step, status=plan_status)

    def test_merged_everywhere_satisfies_the_step(self) -> None:
        plan = self._awaiting()
        self.probe.set('T-1', 'api', PullRequestFact('merged', 'u-1', LATER))
        self.ticker.tick()
        step = self._step(plan, 'a')
        self.assertEqual((step.status, step.satisfied_by), (StepStatus.SATISFIED, 'merged'))
        self.assertEqual([repo.state for repo in step.merge.repos],
                         [RepoMergeState.MERGED, RepoMergeState.NONE])
        self.assertIn('T-1: merged', self.events)

    def test_declined_fails_the_step_naming_the_request(self) -> None:
        plan = self._awaiting()
        self.probe.set('T-1', 'api', PullRequestFact('declined', 'u-1', LATER))
        self.ticker.tick()
        step = self._step(plan, 'a')
        self.assertEqual((step.status, step.reason_code), (StepStatus.FAILED, 'pr_declined'))
        self.assertIn('u-1', step.reason)

    def test_an_open_request_waits_with_backoff(self) -> None:
        plan = self._awaiting()
        self.probe.set('T-1', 'web', PullRequestFact('open', 'u-2', LATER))
        self.ticker.tick()
        step = self._step(plan, 'a')
        self.assertEqual((step.status, step.reason), (StepStatus.AWAITING_MERGE, MERGE_WAIT_REASON))
        self.assertEqual(step.merge.attempts, 1)
        self.assertEqual(step.merge.next_check_at, 1_000.0 + PROBE_BACKOFF_SECONDS[0])
        self.ticker.tick()  # not due yet
        self.assertEqual(len(self.probe.calls), 2)
        self.clock.advance(PROBE_BACKOFF_SECONDS[0])
        self.ticker.tick()
        self.assertEqual(len(self.probe.calls), 4)
        self.assertEqual(self._step(plan, 'a').merge.next_check_at,
                         self.clock.now + PROBE_BACKOFF_SECONDS[1])

    def test_a_host_that_cannot_be_asked_is_unknown_not_none(self) -> None:
        plan = self._awaiting()
        self.probe.failing.add(('T-1', 'api'))
        self.probe.set('T-1', 'web', PullRequestFact('merged', 'u', LATER))
        with self.assertLogs('PlanTicker', level='WARNING'):
            self.ticker.tick()
        step = self._step(plan, 'a')
        self.assertEqual(step.status, StepStatus.AWAITING_MERGE)
        self.assertEqual(step.merge.repos[0].state, RepoMergeState.UNKNOWN)

    def test_an_old_merged_request_on_a_reused_branch_does_not_count(self) -> None:
        plan = self._awaiting()
        self.probe.set('T-1', 'api', PullRequestFact('merged', 'old', '2000-01-01T00:00:00Z'))
        self.ticker.tick()
        self.assertEqual(self._step(plan, 'a').status, StepStatus.AWAITING_MERGE)

    def test_probes_per_tick_are_capped(self) -> None:
        steps = [Step(id=f's{i}', title='S', brief='b', status=StepStatus.AWAITING_MERGE,
                      task_id=f'T-{i}', repositories=['api']) for i in range(MAX_PROBES_PER_TICK + 2)]
        self._plan(*steps)
        self.ticker.tick()
        self.assertEqual(len(self.probe.calls), MAX_PROBES_PER_TICK)

    def test_check_now_on_a_step_not_done_only_records_what_it_saw(self) -> None:
        plan = self._plan(Step(id='a', title='A', brief='b', status=StepStatus.IN_REVIEW, task_id='T-1',
                               repositories=['api']))
        self.registry.mutate(plan.plan_id, lambda live: setattr(live.step('a').merge, 'forced', True))
        self.probe.set('T-1', 'api', PullRequestFact('merged', 'u', LATER))
        self.ticker.tick()
        step = self._step(plan, 'a')
        self.assertEqual(step.status, StepStatus.IN_REVIEW)
        self.assertEqual(step.merge.repos[0].state, RepoMergeState.MERGED)
        self.assertFalse(step.merge.forced)

    def test_a_step_without_a_task_is_never_probed(self) -> None:
        self._plan(Step(id='a', title='A', brief='b', status=StepStatus.AWAITING_MERGE))
        self.ticker.tick()
        self.assertEqual(self.probe.calls, [])

    def test_a_probe_result_for_a_step_that_moved_is_dropped(self) -> None:
        plan = self._awaiting()
        self.probe.set('T-1', 'api', PullRequestFact('merged', 'u', LATER))
        original = self.probe.pull_requests

        def probe_then_operator_acts(task_id, repository_id):
            self.registry.mutate(plan.plan_id, lambda live: setattr(live.step('a'), 'status',
                                                                    StepStatus.CANCELLED))
            return original(task_id, repository_id)

        self.probe.pull_requests = probe_then_operator_acts
        self.ticker.tick()
        self.assertEqual(self._step(plan, 'a').status, StepStatus.CANCELLED)

    # ----- unblock + release -----

    def test_a_satisfied_parent_unblocks_and_releases_its_child(self) -> None:
        plan = self._plan(
            Step(id='a', title='A', brief='b', status=StepStatus.SATISFIED, task_id='T-1'),
            Step(id='b', title='B', brief='b', depends_on=['a'], status=StepStatus.BLOCKED, task_id='T-2'),
            Step(id='c', title='C', brief='b', depends_on=['b'], status=StepStatus.BLOCKED),
        )
        self.ticker.tick()
        b, c = self._step(plan, 'b'), self._step(plan, 'c')
        self.assertEqual((b.status, b.task_id, b.released_at), (StepStatus.STARTING, 'T-2', 1_000.0))
        self.assertEqual((c.status, c.reason), (StepStatus.BLOCKED, 'waits for T-2'))
        request, task_id = self.launcher.released[0]
        self.assertEqual((request.step_id, task_id), ('b', 'T-2'))
        self.assertIn('released T-2', self.events)

    def test_a_paused_or_approved_plan_releases_nothing(self) -> None:
        for status in (PlanStatus.PAUSED, PlanStatus.APPROVED):
            plan = self._plan(Step(id='a', title='A', brief='b', status=StepStatus.READY), status=status)
            self.ticker.tick()
            self.assertEqual(self._step(plan, 'a').status, StepStatus.READY)
        self.assertEqual(self.launcher.released, [])

    def test_no_launcher_or_an_unavailable_one_releases_nothing(self) -> None:
        plan = self._plan(Step(id='a', title='A', brief='b', status=StepStatus.READY))
        self.launcher.is_available = False
        self.ticker.tick()
        self.launcher.available = mock.Mock(side_effect=RuntimeError('broken'))
        self.ticker.tick()
        self.ticker._launcher = lambda plan: None
        self.ticker.tick()
        self.assertEqual(self._step(plan, 'a').status, StepStatus.READY)

    def test_a_refused_or_failed_release_fails_the_step(self) -> None:
        plan = self._plan(Step(id='a', title='A', brief='b', status=StepStatus.READY),
                          Step(id='b', title='B', brief='b', status=StepStatus.READY))
        self.launcher.release_errors['a'] = LaunchRefused('repo not approved')
        self.launcher.release_errors['b'] = RuntimeError('crash')
        self.ticker.tick()
        a, b = self._step(plan, 'a'), self._step(plan, 'b')
        self.assertEqual((a.status, a.reason_code, a.reason), (StepStatus.FAILED, 'launch_refused', 'repo not approved'))
        self.assertEqual((b.status, b.reason_code), (StepStatus.FAILED, 'launch_failed'))
        self.assertIn('could not start a: repo not approved', self.events)

    def test_a_release_that_creates_a_new_task_keeps_the_old_id(self) -> None:
        self.launcher.creates_at_approval = False
        plan = self._plan(Step(id='a', title='A', brief='b', status=StepStatus.READY, task_id='L-9'))
        self.ticker.tick()
        step = self._step(plan, 'a')
        self.assertEqual((step.task_id, step.previous_task_ids), ('T-101', ['L-9']))

    def test_a_claim_lost_to_the_operator_releases_nothing(self) -> None:
        plan = self._plan(Step(id='a', title='A', brief='b', status=StepStatus.READY))
        original = self.registry.get

        def get_then_pause(plan_id):
            result = original(plan_id)
            self.registry.mutate(plan_id, lambda live: setattr(live, 'status', PlanStatus.PAUSED))
            return result

        with mock.patch.object(self.registry, 'get', side_effect=get_then_pause):
            self.ticker._release(plan.plan_id)
        self.assertEqual(self.launcher.released, [])

    def test_a_release_result_for_a_step_that_moved_is_dropped(self) -> None:
        plan = self._plan(Step(id='a', title='A', brief='b', status=StepStatus.READY))
        original = self.launcher.release

        def release_then_cancel(request, task_id):
            self.registry.mutate(plan.plan_id, lambda live: setattr(live.step('a'), 'status',
                                                                    StepStatus.CANCELLED))
            return original(request, task_id)

        self.launcher.release = release_then_cancel
        self.ticker.tick()
        self.assertEqual(self._step(plan, 'a').status, StepStatus.CANCELLED)

    # ----- finish + misc -----

    def test_a_plan_with_every_step_settled_is_finished(self) -> None:
        plan = self._plan(Step(id='a', title='A', brief='b', status=StepStatus.SATISFIED),
                          Step(id='b', title='B', brief='b', status=StepStatus.CANCELLED))
        self.ticker.tick()
        self.assertEqual(self.registry.get(plan.plan_id).status, PlanStatus.FINISHED)
        self.assertIn('finished — every step is done', self.events)

    def test_plans_without_steps_in_flight_are_skipped(self) -> None:
        plan = self._plan(Step(id='a', title='A', brief='b', status=StepStatus.READY),
                          status=PlanStatus.PROPOSED)
        self.ticker.tick()
        self.assertEqual(self._step(plan, 'a').status, StepStatus.READY)

    def test_a_failing_observer_is_logged_not_raised(self) -> None:
        self.ticker._observer = mock.Mock(side_effect=RuntimeError('observer bug'))
        self._plan(Step(id='a', title='A', brief='b', status=StepStatus.SATISFIED))
        with self.assertLogs('PlanTicker', level='ERROR'):
            self.ticker.tick()

    def test_notes_without_an_observer_or_a_plan_are_skipped(self) -> None:
        self.ticker._note('f' * 32, 'nothing')
        self.ticker._observer = None
        self._plan(Step(id='a', title='A', brief='b', status=StepStatus.SATISFIED))
        self.ticker.tick()


if __name__ == '__main__':
    unittest.main()
