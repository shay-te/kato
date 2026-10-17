"""Every operator action, and what each one refuses."""

from __future__ import annotations

import tempfile
import unittest
from unittest import mock

from plan_queue_core_lib.plan_queue_core_lib.data.plan import FailReason, PlanStatus, StepStatus
from plan_queue_core_lib.plan_queue_core_lib.errors import ErrorKind, PlanQueueError
from plan_queue_core_lib.plan_queue_core_lib.graph import StepDraft
from plan_queue_core_lib.plan_queue_core_lib.service import (
    MAX_FEEDBACK_CHARS,
    MAX_PLAN_CHARS,
    PlanQueueService,
)
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

SPLIT = split_reply([
    step_spec('api', repositories=['api']),
    step_spec('ui', depends_on=['api'], repositories=['web']),
    step_spec('docs'),
])


class ServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix='plan-service-')
        self.addCleanup(tmp.cleanup)
        self.store = PlanQueueStore(tmp.name)
        self.clock = ManualClock(5_000.0)
        self.proposer = FakeProposer(SPLIT)
        self.launcher = FakeLauncher()
        self.progress = FakeProgress()
        self.probe = FakeProbe()
        self.events: list[str] = []
        self.service = self._service()
        self.addCleanup(self.service.shutdown)

    def _service(self) -> PlanQueueService:
        service = PlanQueueService(
            store=self.store, proposer=self.proposer, wording=FakeWording(),
            progress=self.progress, probe=self.probe, clock=self.clock,
            observer=lambda plan, event: self.events.append(event), tick_seconds=0.01,
        )
        service.bind_launcher(self.launcher)
        return service

    def _proposed(self, source: str = 'SRC-1'):
        plan = self.service.propose(source, plan_markdown='# The plan', repository_ids=['api', 'web'],
                                    title='Discounts', model='opus')
        self.assertTrue(wait_until(lambda: self.service.get(plan.plan_id).status == PlanStatus.PROPOSED))
        return self.service.get(plan.plan_id)

    def _approved(self, **kwargs):
        plan = self._proposed()
        return self.service.approve(plan.plan_id, **kwargs)

    # ----- proposing -----

    def test_propose_refuses_bad_input(self) -> None:
        cases = [
            dict(plan_markdown='  ', repository_ids=['api']),
            dict(plan_markdown='x' * (MAX_PLAN_CHARS + 1), repository_ids=['api']),
            dict(plan_markdown='plan', repository_ids=['', '']),
        ]
        for case in cases:
            with self.assertRaises(PlanQueueError) as caught:
                self.service.propose('SRC-1', **case)
            self.assertEqual(caught.exception.kind, ErrorKind.INVALID)

    def test_the_host_can_refuse_a_proposal(self) -> None:
        self.service._can_propose = lambda task_id: f'{task_id} has no clone'
        with self.assertRaises(PlanQueueError) as caught:
            self.service.propose('SRC-1', plan_markdown='p', repository_ids=['api'])
        self.assertEqual((str(caught.exception), caught.exception.kind),
                         ('SRC-1 has no clone', ErrorKind.CONFLICT))
        self.service._can_propose = lambda task_id: ''
        self.service.propose('SRC-1', plan_markdown='p', repository_ids=['api'])

    def test_one_open_plan_per_source_task(self) -> None:
        plan = self._proposed('SRC-1')
        with self.assertRaises(PlanQueueError) as caught:
            self.service.propose('src-1', plan_markdown='again', repository_ids=['api'])
        self.assertIn('already has a plan', str(caught.exception))
        self.service.stop(plan.plan_id)
        again = self.service.propose('SRC-1', plan_markdown='again', repository_ids=['api'])
        self.assertEqual(self.service.plan_for_source('src-1').plan_id, again.plan_id)
        self.assertIsNone(self.service.plan_for_source('OTHER'))

    def test_revise_and_retry_proposal(self) -> None:
        plan = self._proposed()
        with self.assertRaises(PlanQueueError):
            self.service.revise(plan.plan_id, '   ')
        with self.assertRaises(PlanQueueError):
            self.service.revise(plan.plan_id, 'x' * (MAX_FEEDBACK_CHARS + 1))
        revised = self.service.revise(plan.plan_id, 'fewer steps')
        self.assertEqual((revised.revision, revised.revisions[0].feedback), (1, 'fewer steps'))
        self.assertTrue(wait_until(lambda: self.service.get(plan.plan_id).status == PlanStatus.PROPOSED))
        self.assertIn('fewer steps', self.proposer.prompts[-1])
        with self.assertRaises(PlanQueueError):
            self.service.retry_proposal(plan.plan_id)  # only after a failure
        self.proposer.error = RuntimeError('agent down')
        self.service.revise(plan.plan_id, 'again')
        self.assertTrue(wait_until(lambda: self.service.get(plan.plan_id).status == PlanStatus.PROPOSE_FAILED))
        self.proposer.error = None
        self.service.retry_proposal(plan.plan_id)
        self.assertTrue(wait_until(lambda: self.service.get(plan.plan_id).status == PlanStatus.PROPOSED))

    def test_edit_uses_the_same_validator(self) -> None:
        plan = self._proposed()
        drafts = [StepDraft('one', 'One', 'brief', (), ('api',)), StepDraft('two', 'Two', 'brief', ('one',))]
        edited = self.service.edit(plan.plan_id, drafts, title='  New title  ')
        self.assertEqual([step.id for step in edited.steps], ['one', 'two'])
        self.assertEqual(edited.steps[1].repositories, ['api', 'web'])
        self.assertEqual(edited.title, 'New title')
        with self.assertRaises(PlanQueueError) as caught:
            self.service.edit(plan.plan_id, [StepDraft('one', 'One', 'b', ('nope',))])
        self.assertEqual(caught.exception.kind, ErrorKind.INVALID)
        self.service.edit(plan.plan_id, drafts, title='   ')
        self.assertEqual(self.service.get(plan.plan_id).title, 'New title')

    # ----- approving -----

    def test_approve_creates_the_tasks_parents_first(self) -> None:
        plan = self._approved(start_mode='implement', model='sonnet', effort='high')
        self.assertEqual(plan.status, PlanStatus.APPROVED)
        self.assertEqual([request.step_id for request in self.launcher.prepared], ['api', 'docs', 'ui'])
        ui_request = self.launcher.prepared[2]
        self.assertIn('T-101: Do api', ui_request.description)  # names its parent's task
        self.assertEqual((ui_request.model, ui_request.effort, ui_request.start_mode),
                         ('sonnet', 'high', 'implement'))
        api, ui, docs = plan.steps
        self.assertEqual((api.status, ui.status, docs.status),
                         (StepStatus.READY, StepStatus.BLOCKED, StepStatus.READY))
        self.assertEqual(ui.reason, 'waits for Do api')
        self.assertEqual(plan.model, 'opus')  # the proposer's model is kept apart
        self.assertIn('created T-101 for step api', self.events)

    def test_approve_and_run(self) -> None:
        plan = self._approved(run=True)
        self.assertEqual(plan.status, PlanStatus.RUNNING)

    def test_approve_records_a_failed_create(self) -> None:
        plan = self._proposed()
        self.launcher.prepare_errors['ui'] = RuntimeError('tracker said no')
        approved = self.service.approve(plan.plan_id)
        ui = approved.step('ui')
        self.assertEqual((ui.status, ui.reason_code, ui.reason),
                         (StepStatus.FAILED, 'create_failed', 'tracker said no'))
        self.assertIn('could not create step ui: tracker said no', self.events)

    def test_a_host_that_creates_at_release_creates_nothing_at_approval(self) -> None:
        self.launcher.creates_at_approval = False
        self._approved()
        self.assertEqual(self.launcher.prepared, [])

    def test_approve_needs_a_launcher_and_a_proposed_plan(self) -> None:
        plan = self._proposed()
        self.service.bind_launcher(None)
        with self.assertRaises(PlanQueueError) as caught:
            self.service.approve(plan.plan_id)
        self.assertEqual(caught.exception.kind, ErrorKind.UNAVAILABLE)
        self.service.bind_launcher(self.launcher)
        self.launcher.is_available = False
        with self.assertRaises(PlanQueueError):
            self.service.approve(plan.plan_id)
        self.launcher.available = mock.Mock(side_effect=RuntimeError('broken'))
        with self.assertRaises(PlanQueueError):
            self.service.approve(plan.plan_id)
        self.launcher = FakeLauncher()
        self.service.bind_launcher(self.launcher)
        self.service.approve(plan.plan_id)
        with self.assertRaises(PlanQueueError) as caught:
            self.service.approve(plan.plan_id)
        self.assertIn('this needs it proposed', str(caught.exception))

    def test_approve_refuses_an_empty_split(self) -> None:
        plan = self._proposed()
        self.service._registry.mutate(plan.plan_id, lambda live: setattr(live, 'steps', []))
        with self.assertRaises(PlanQueueError) as caught:
            self.service.approve(plan.plan_id)
        self.assertEqual(caught.exception.kind, ErrorKind.INVALID)

    # ----- running -----

    def test_run_pause_run(self) -> None:
        plan = self._approved()
        self.assertEqual(self.service.run(plan.plan_id).status, PlanStatus.RUNNING)
        with self.assertRaises(PlanQueueError):
            self.service.run(plan.plan_id)
        paused = self.service.pause(plan.plan_id)
        self.assertEqual((paused.status, paused.reason), (PlanStatus.PAUSED, 'paused by the operator'))
        with self.assertRaises(PlanQueueError):
            self.service.pause(plan.plan_id)
        self.assertEqual(self.service.run(plan.plan_id).reason, '')

    def test_stop_cancels_what_never_started_and_leaves_the_rest(self) -> None:
        plan = self._approved(run=True)
        self.service.tick()  # api and docs are released
        stopped = self.service.stop(plan.plan_id)
        self.assertEqual(stopped.status, PlanStatus.CANCELLED)
        self.assertEqual([step.status for step in stopped.steps],
                         [StepStatus.STARTING, StepStatus.CANCELLED, StepStatus.STARTING])
        self.assertEqual(self.launcher.withdrawn, [('T-103', 'the plan was stopped')])
        with self.assertRaises(PlanQueueError):
            self.service.stop(plan.plan_id)

    def test_stopping_a_proposal_cancels_it(self) -> None:
        import threading
        gate = threading.Event()
        self.proposer.gate = gate
        plan = self.service.propose('SRC-1', plan_markdown='p', repository_ids=['api'])
        self.service.stop(plan.plan_id)
        gate.set()
        self.service._proposals.join(plan.plan_id, timeout=5)
        self.assertEqual(self.service.get(plan.plan_id).status, PlanStatus.CANCELLED)

    def test_discard(self) -> None:
        plan = self._approved(run=True)
        with self.assertRaises(PlanQueueError):
            self.service.discard(plan.plan_id)
        self.service.pause(plan.plan_id)
        self.service.discard(plan.plan_id)
        # tasks were created parents first: api T-101, docs T-102, ui T-103
        self.assertEqual([task for task, _ in self.launcher.withdrawn], ['T-101', 'T-103', 'T-102'])
        with self.assertRaises(PlanQueueError) as caught:
            self.service.get(plan.plan_id)
        self.assertEqual(caught.exception.kind, ErrorKind.NOT_FOUND)

    # ----- one step -----

    def test_mark_satisfied_releases_its_dependents(self) -> None:
        plan = self._approved(run=True)
        done = self.service.mark_satisfied(plan.plan_id, 'api')
        api = done.step('api')
        self.assertEqual((api.status, api.satisfied_by), (StepStatus.SATISFIED, 'operator'))
        self.assertEqual(self.launcher.withdrawn, [('T-101', 'counted as done without running')])
        self.service.tick()
        self.assertEqual(self.service.get(plan.plan_id).step('ui').status, StepStatus.STARTING)
        with self.assertRaises(PlanQueueError):
            self.service.mark_satisfied(plan.plan_id, 'api')
        with self.assertRaises(PlanQueueError) as caught:
            self.service.mark_satisfied(plan.plan_id, 'nope')
        self.assertEqual(caught.exception.kind, ErrorKind.NOT_FOUND)

    def test_step_actions_need_steps_in_flight(self) -> None:
        plan = self._proposed()
        for action in (self.service.mark_satisfied, self.service.cancel_step, self.service.retry_step):
            with self.assertRaises(PlanQueueError):
                action(plan.plan_id, 'api')

    def test_cancel_step(self) -> None:
        plan = self._approved()
        cancelled = self.service.cancel_step(plan.plan_id, 'docs')
        self.assertEqual(cancelled.step('docs').status, StepStatus.CANCELLED)
        self.assertEqual(self.launcher.withdrawn, [('T-102', 'the step was cancelled')])
        with self.assertRaises(PlanQueueError):
            self.service.cancel_step(plan.plan_id, 'docs')

    def _fail(self, plan_id, step_id, code, **fields):
        def apply(live):
            step = live.step(step_id)
            step.status = StepStatus.FAILED
            step.reason_code = code
            for key, value in fields.items():
                setattr(step, key, value)
        self.service._registry.mutate(plan_id, apply)

    def test_retry_a_declined_pull_request_looks_again(self) -> None:
        plan = self._approved()
        self._fail(plan.plan_id, 'api', FailReason.PR_DECLINED.value, released_at=1.0)
        step = self.service.retry_step(plan.plan_id, 'api').step('api')
        self.assertEqual((step.status, step.merge.next_check_at), (StepStatus.AWAITING_MERGE, 0.0))

    def test_retry_a_create_failure_creates_a_new_task(self) -> None:
        plan = self._approved()
        self._fail(plan.plan_id, 'ui', FailReason.TASK_GONE.value)
        step = self.service.retry_step(plan.plan_id, 'ui').step('ui')
        self.assertEqual((step.task_id, step.previous_task_ids), ('T-104', ['T-103']))
        self.assertEqual(step.status, StepStatus.BLOCKED)

    def test_retry_a_refused_launch_keeps_the_task(self) -> None:
        plan = self._approved()
        self._fail(plan.plan_id, 'docs', FailReason.LAUNCH_REFUSED.value)
        step = self.service.retry_step(plan.plan_id, 'docs').step('docs')
        self.assertEqual((step.status, step.task_id), (StepStatus.READY, 'T-102'))
        with self.assertRaises(PlanQueueError):
            self.service.retry_step(plan.plan_id, 'docs')  # not failed any more

    def test_retry_without_a_launcher_only_resets_the_step(self) -> None:
        plan = self._approved()
        self._fail(plan.plan_id, 'docs', FailReason.CREATE_FAILED.value)
        self.service.bind_launcher(None)
        step = self.service.retry_step(plan.plan_id, 'docs').step('docs')
        self.assertEqual((step.status, step.task_id), (StepStatus.READY, ''))

    def test_preparing_a_step_that_already_has_a_task_is_a_no_op(self) -> None:
        plan = self._approved()
        before = len(self.launcher.prepared)
        self.service._prepare_one(plan.plan_id, 'api', self.launcher)
        self.service._prepare_one(plan.plan_id, 'missing', self.launcher)
        self.assertEqual(len(self.launcher.prepared), before)

    def test_a_prepare_result_for_a_step_cancelled_meanwhile_is_dropped(self) -> None:
        self.launcher.creates_at_approval = False
        plan = self._approved()
        original = self.launcher.prepare

        def prepare_then_cancel(request):
            self.service._registry.mutate(plan.plan_id, lambda live: setattr(
                live.step(request.step_id), 'status', StepStatus.CANCELLED))
            return original(request)

        self.launcher.prepare = prepare_then_cancel
        self.service._prepare_one(plan.plan_id, 'docs', self.launcher)
        self.assertEqual(self.service.get(plan.plan_id).step('docs').task_id, '')

    def test_check_now(self) -> None:
        plan = self._approved(run=True)
        with self.assertRaises(PlanQueueError):
            self.service.check_now(plan.plan_id, 'ui')  # not started
        self.service.tick()
        step = self.service.check_now(plan.plan_id, 'api').step('api')
        self.assertTrue(step.merge.forced)

    # ----- reading, lifecycle -----

    def test_summaries_and_plans(self) -> None:
        plan = self._approved()
        [summary] = self.service.summaries()
        self.assertEqual((summary['plan_id'], summary['status'], summary['step_count']),
                         (plan.plan_id, 'approved', 3))
        self.assertEqual(summary['counts']['ready'], 2)
        self.assertEqual([p.plan_id for p in self.service.plans()], [plan.plan_id])

    def test_live_by_source_names_each_tasks_newest_unfinished_plan(self) -> None:
        self.assertEqual(self.service.live_by_source(), {})
        first = self._approved()
        self.service._registry.mutate(first.plan_id, lambda live: setattr(live, 'status', PlanStatus.CANCELLED))
        self.assertEqual(self.service.live_by_source(), {})  # stopped: nothing to show
        second = self._approved()
        live = self.service.live_by_source()[second.source_task_id]
        self.assertEqual((live['plan_id'], live['status'], live['step_count'], live['counts']['ready']),
                         (second.plan_id, 'approved', 3, 2))

    def test_mark_interrupted_after_a_restart(self) -> None:
        running = self._approved(run=True)
        self.service._registry.mutate(running.plan_id, lambda live: (
            setattr(live.step('docs'), 'status', StepStatus.STARTING),
            setattr(live.step('docs'), 'task_id', ''),
        ))
        import threading
        gate = threading.Event()
        self.proposer.gate = gate
        proposing = self.service.propose('SRC-2', plan_markdown='p', repository_ids=['api'])
        approved = self.service.propose('SRC-3', plan_markdown='p', repository_ids=['api'])
        self.service._registry.mutate(approved.plan_id, lambda live: setattr(live, 'status', PlanStatus.APPROVED))
        restarted = self._service()
        restarted.mark_interrupted('interrupted by a restart', proposal_reason='the split was cut off')
        self.assertEqual(restarted.get(approved.plan_id).status, PlanStatus.APPROVED)  # untouched
        self.assertEqual(restarted.get(running.plan_id).status, PlanStatus.PAUSED)
        self.assertEqual(restarted.get(running.plan_id).reason, 'interrupted by a restart')
        self.assertEqual(restarted.get(proposing.plan_id).reason, 'the split was cut off')
        self.assertEqual(restarted.get(running.plan_id).step('docs').status, StepStatus.READY)
        self.assertEqual(restarted.get(proposing.plan_id).status, PlanStatus.PROPOSE_FAILED)
        gate.set()

    def test_an_edit_may_add_a_repository_every_task_works_in(self) -> None:
        plan = self._proposed()
        drafts = [StepDraft(step.id, step.title, step.brief, depends_on=tuple(step.depends_on),
                            repositories=tuple(step.repositories) + ('core',)) for step in plan.steps]
        with self.assertRaises(PlanQueueError):  # not one of the plan's repositories
            self.service.edit(plan.plan_id, drafts)
        edited = self.service.edit(plan.plan_id, drafts, repositories=['core', ' ', 'api', 'core'])
        self.assertEqual(edited.repository_ids, ['api', 'web', 'core'])
        self.assertTrue(all('core' in step.repositories for step in edited.steps))

    def test_start_runs_the_tick_on_a_thread(self) -> None:
        plan = self._approved(run=True)
        self.assertTrue(self.service.start())
        self.assertFalse(self.service.start())
        self.assertTrue(wait_until(lambda: self.launcher.released))
        self.service.shutdown()
        self.assertEqual(self.service.get(plan.plan_id).step('api').status, StepStatus.STARTING)

    def test_a_failing_observer_is_logged_not_raised(self) -> None:
        self.service._observer = mock.Mock(side_effect=RuntimeError('observer bug'))
        plan = self._proposed()
        with self.assertLogs('PlanQueueService', level='ERROR'):
            self.service.approve(plan.plan_id)
        self.service._observer = None
        self.service._note(plan.plan_id, 'nothing happens')
        self.service._observer = mock.Mock()
        self.service._note('f' * 32, 'unknown plan')
        self.service._observer.assert_not_called()


if __name__ == '__main__':
    unittest.main()
