"""A plan on a shared tracker, seen by two hosts — the operator's and a
teammate's — over ONE tracker.

What must hold:
- the tracker is the record: sub-tasks are created unassigned, and the plan
  (who waits for whom, who drives) is published where both hosts read it;
- a sub-task belongs to whoever has it: a teammate who takes one (even before
  its turn) shows on both boards and is never assigned over;
- only the driver releases; the other host's board is read-only until it
  takes the plan over;
- "merged and done" is read from shared facts (the tracker's state, the git
  host's pull requests, the destination's history), so both boards agree;
- a record that was forged or rewrites the order is not followed.
"""

from __future__ import annotations

import tempfile
import unittest

from plan_queue_core_lib.plan_queue_core_lib.data.plan import (
    PlanMode,
    PlanStatus,
    SatisfiedBy,
    StepStatus,
)
from plan_queue_core_lib.plan_queue_core_lib.errors import ErrorKind, PlanQueueError
from plan_queue_core_lib.plan_queue_core_lib.plan_record import parse_record, render_record
from plan_queue_core_lib.plan_queue_core_lib.ports import PullRequestFact, RecordStep
from plan_queue_core_lib.plan_queue_core_lib.progress import HANDED_BACK_REASON, TAKEN_EARLY_REASON
from plan_queue_core_lib.plan_queue_core_lib.service import PlanQueueService
from plan_queue_core_lib.plan_queue_core_lib.store import PlanQueueStore
from plan_queue_core_lib.plan_queue_core_lib.ticking import NO_PR_REASON, PUBLISH_RETRY_SECONDS
from plan_queue_core_lib.plan_queue_core_lib.tests.fakes import (
    FakeHistory,
    FakeOwnership,
    FakeProbe,
    FakeProposer,
    FakeTracker,
    FakeWording,
    ManualClock,
    split_reply,
    step_spec,
    wait_until,
)

SOURCE = 'PROJ-1'
SPLIT = split_reply([
    step_spec('api', repositories=['api']),
    step_spec('ui', depends_on=['api'], repositories=['web']),
    step_spec('docs', depends_on=['api']),  # no repositories named: every one of the plan's
])


def iso(epoch: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


class Host(object):
    """One host (a teammate's machine) over the shared tracker."""

    def __init__(self, test, tracker: FakeTracker, *, identity: str, user: str, mode=PlanMode.TRACKER) -> None:
        tmp = tempfile.TemporaryDirectory(prefix=f'plan-shared-{user}-')
        test.addCleanup(tmp.cleanup)
        self.user = user
        self.events: list[str] = []
        self.ownership = FakeOwnership(identity, user)
        self.publisher = tracker.publisher(user)
        self.launcher = tracker.launcher(user)
        self.service = PlanQueueService(
            store=PlanQueueStore(tmp.name), proposer=FakeProposer(SPLIT), wording=FakeWording(),
            progress=tracker, probe=test.probe, clock=test.clock, tick_seconds=60,
            observer=lambda plan, event: self.events.append(event),
            mode_for=lambda plan: mode, ownership=self.ownership, publisher=self.publisher,
            record_source=tracker, history=test.history,
        )
        self.service.bind_launcher(self.launcher, mode=PlanMode.TRACKER)
        test.addCleanup(self.service.shutdown)

    def tick(self):
        self.service.tick()
        return self

    def plan(self, plan_id):
        return self.service.get(plan_id)

    def step(self, plan_id, step_id):
        return self.service.get(plan_id).step(step_id)


class SharedPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = ManualClock(1_000_000.0)
        self.probe = FakeProbe()
        self.history = FakeHistory()
        self.tracker = FakeTracker(self.clock)
        self.shay = Host(self, self.tracker, identity='host-shay', user='shay')
        self.dana = Host(self, self.tracker, identity='host-dana', user='dana')

    # ----- helpers -----

    def _approved(self, *, run: bool = True) -> str:
        plan = self.shay.service.propose(SOURCE, plan_markdown='# plan', repository_ids=['api', 'web'], title='T')
        self.assertTrue(wait_until(lambda: self.shay.plan(plan.plan_id).status == PlanStatus.PROPOSED))
        self.shay.service.approve(plan.plan_id, run=run, shared=True)
        self.shay.tick()
        return plan.plan_id

    def _task(self, plan_id, step_id, host=None) -> str:
        return (host or self.shay).step(plan_id, step_id).task_id

    def _merged(self, plan_id, step_id, *, repos=('api',), created_after: float = 1) -> None:
        task_id = self._task(plan_id, step_id)
        self.tracker.move(task_id, 'done')
        created = self.tracker.tasks[task_id]['created'] + created_after
        for repo in repos:
            self.probe.set(task_id, repo, PullRequestFact('merged', f'https://git/{task_id}', iso(created)))

    def _followed(self, plan_id) -> str:
        followed = self.dana.service.follow(self.tracker.latest(SOURCE), title='T')
        self.assertEqual(followed.plan_id, plan_id)
        return plan_id

    # ----- the record -----

    def test_approve_creates_unassigned_subtasks_and_publishes_the_plan(self) -> None:
        plan_id = self._approved(run=False)
        plan = self.shay.plan(plan_id)
        self.assertEqual((plan.mode, plan.driver, plan.driver_user), (PlanMode.TRACKER, 'host-shay', 'shay'))
        self.assertEqual(len(self.tracker.tasks), 3)
        self.assertTrue(all(task['assignee'] == '' for task in self.tracker.tasks.values()))
        api, ui = self._task(plan_id, 'api'), self._task(plan_id, 'ui')
        self.assertEqual(self.tracker.tasks[ui]['waits_for'], (api,))  # parents first: the footer names them
        record = self.tracker.latest(SOURCE)
        self.assertEqual((record.plan_id, record.author, record.driver, record.status),
                         (plan_id, 'shay', 'host-shay', 'approved'))
        self.assertEqual([(s.step_id, s.waits_for, s.repositories, s.done) for s in record.steps],
                         [('api', (), ('api',), False), ('ui', (api,), ('web',), False),
                          ('docs', (api,), ('api', 'web'), False)])
        self.assertEqual(plan.published_version, plan.record_version)
        self.shay.tick()
        self.assertEqual(self.shay.publisher.published, 1)  # nothing changed: nothing posted again

    def test_run_releases_to_the_driver_and_keeps_the_order(self) -> None:
        plan_id = self._approved()
        api, ui = self._task(plan_id, 'api'), self._task(plan_id, 'ui')
        self.assertEqual(self.tracker.tasks[api]['assignee'], 'shay')
        self.assertEqual(self.tracker.tasks[ui]['assignee'], '')
        self.assertEqual(self.tracker.latest(SOURCE).status, 'running')
        self.shay.tick()
        self.assertEqual(self.shay.step(plan_id, 'api').worker, 'Shay')

    # ----- a teammate takes a sub-task -----

    def test_a_teammate_who_takes_a_waiting_subtask_shows_and_is_never_assigned_over(self) -> None:
        plan_id = self._approved()
        ui = self._task(plan_id, 'ui')
        self.tracker.take(ui, 'dana', 'Dana')
        self.shay.tick()
        step = self.shay.step(plan_id, 'ui')
        self.assertEqual((step.status, step.reason, step.worker, step.worker_handle),
                         (StepStatus.STARTING, TAKEN_EARLY_REASON, 'Dana', 'dana'))
        self.assertTrue(step.released_at)
        self._merged(plan_id, 'api')
        self.shay.tick()
        self.assertEqual(self.tracker.tasks[ui]['assignee'], 'dana')  # still hers
        self.assertEqual(self.tracker.tasks[self._task(plan_id, 'docs')]['assignee'], 'shay')

    def test_the_teammates_host_follows_the_same_board_read_only(self) -> None:
        plan_id = self._followed(self._approved())
        followed = self.dana.plan(plan_id)
        self.assertEqual((followed.mode, followed.driver_user, followed.status),
                         (PlanMode.FOLLOWED, 'shay', PlanStatus.RUNNING))
        self.dana.tick()
        self.assertEqual(self.dana.step(plan_id, 'api').status, StepStatus.STARTING)
        self.assertEqual(self.dana.step(plan_id, 'ui').status, StepStatus.BLOCKED)
        refused = [
            lambda: self.dana.service.run(plan_id),
            lambda: self.dana.service.pause(plan_id),
            lambda: self.dana.service.stop(plan_id),
            lambda: self.dana.service.mark_satisfied(plan_id, 'api'),
            lambda: self.dana.service.cancel_step(plan_id, 'ui'),
            lambda: self.dana.service.retry_step(plan_id, 'ui'),
        ]
        for call in refused:
            with self.assertRaises(PlanQueueError) as caught:
                call()
            self.assertIn('driven by shay; take it over', str(caught.exception))
        self._merged(plan_id, 'api')
        self.dana.tick()
        self.assertEqual(self.dana.step(plan_id, 'api').status, StepStatus.SATISFIED)
        self.assertEqual(self.dana.step(plan_id, 'ui').status, StepStatus.READY)
        self.assertEqual(self.tracker.tasks[self._task(plan_id, 'ui')]['assignee'], '')  # dana's host never releases
        self.assertEqual(self.dana.publisher.published, 0)

    def test_following_again_refreshes_and_a_summary_names_the_driver_and_tasks(self) -> None:
        plan_id = self._followed(self._approved())
        again = self.dana.service.follow(self.tracker.latest(SOURCE))
        self.assertEqual(again.plan_id, plan_id)
        summary = self.dana.service.summaries()[0]
        self.assertEqual((summary['mode'], summary['driver_user']), ('followed', 'shay'))
        self.assertEqual(summary['task_ids'], [self._task(plan_id, s) for s in ('api', 'ui', 'docs')])
        self.assertEqual(self.dana.service.plan_for_task(self._task(plan_id, 'ui').lower()).plan_id, plan_id)
        self.assertIsNone(self.dana.service.plan_for_task('PROJ-999'))

    # ----- merged and done -----

    def test_merged_and_done_releases_the_dependents(self) -> None:
        plan_id = self._approved()
        self._merged(plan_id, 'api')
        self.shay.tick()
        api = self.shay.step(plan_id, 'api')
        self.assertEqual((api.status, api.satisfied_by), (StepStatus.SATISFIED, SatisfiedBy.MERGED.value))
        for step_id in ('ui', 'docs'):
            self.assertEqual(self.tracker.tasks[self._task(plan_id, step_id)]['assignee'], 'shay')

    def test_a_pull_request_opened_before_the_release_still_counts(self) -> None:
        plan_id = self._approved()
        ui = self._task(plan_id, 'ui')
        self.clock.advance(3600)
        self._merged(plan_id, 'api')
        self.shay.tick()
        # Dana started ui before its turn and opened her PR an hour before the release.
        self.tracker.move(ui, 'done')
        self.probe.set(ui, 'web', PullRequestFact('merged', '', iso(self.tracker.tasks[ui]['created'] + 10)))
        self.shay.tick()
        self.assertEqual(self.shay.step(plan_id, 'ui').status, StepStatus.SATISFIED)

    def test_merged_without_a_pull_request_counts_through_the_history(self) -> None:
        plan_id = self._approved()
        api = self._task(plan_id, 'api')
        self.tracker.move(api, 'done')
        self.history.failing.add((api, 'api'))
        self.shay.tick()
        self.assertEqual(self.shay.step(plan_id, 'api').reason, NO_PR_REASON)
        self.history.failing.clear()
        self.history.merged.add((api, 'api'))
        self.clock.advance(3600)
        self.shay.tick()
        step = self.shay.step(plan_id, 'api')
        self.assertEqual((step.status, step.satisfied_by, step.reason),
                         (StepStatus.SATISFIED, SatisfiedBy.HISTORY.value, 'merged without a pull request'))
        self.assertEqual(self.history.asked[-1], (api, 'api', self.tracker.tasks[api]['created']))

    def test_a_step_merged_straight_into_no_repository_waits_for_count_as_done(self) -> None:
        plan_id = self._approved()
        self._merged(plan_id, 'api')
        self.shay.tick()
        docs = self._task(plan_id, 'docs')
        self.tracker.move(docs, 'done')
        self.shay.tick()
        self.assertEqual(self.shay.step(plan_id, 'docs').reason, NO_PR_REASON)
        self.assertEqual([entry[:2] for entry in self.history.asked], [(docs, 'api'), (docs, 'web')])

    def test_a_host_that_opens_the_plan_after_a_step_is_done_sees_it_done(self) -> None:
        plan_id = self._approved()
        self._merged(plan_id, 'api')
        self.shay.tick()
        self.shay.tick()  # publishes the record with "done"
        self._followed(plan_id)
        step = self.dana.step(plan_id, 'api')
        self.assertEqual((step.status, step.satisfied_by), (StepStatus.SATISFIED, SatisfiedBy.DRIVER.value))
        self.assertEqual(self.dana.step(plan_id, 'ui').status, StepStatus.READY)
        self.assertEqual(self.dana.step(plan_id, 'docs').repositories, ['api', 'web'])

    def test_following_refuses_a_record_that_cannot_be_trusted(self) -> None:
        plan_id = self._approved()
        good = self.tracker.latest(SOURCE)
        forged = type(good)(**{**good.__dict__, 'author': 'mallory'})
        with self.assertRaises(PlanQueueError) as caught:
            self.dana.service.follow(forged)
        self.assertIn('posted by mallory', str(caught.exception))
        self._followed(plan_id)
        reordered = type(good)(**{**good.__dict__, 'steps': tuple(
            RecordStep(s.task_id, s.step_id, s.title, (), s.repositories) for s in good.steps)})
        with self.assertRaises(PlanQueueError) as caught:
            self.dana.service.follow(reordered)
        self.assertIn('changes which step waits for which', str(caught.exception))

    def test_what_a_task_still_waits_for_and_following_it_from_the_tracker(self) -> None:
        plan_id = self._approved()
        api, ui = self._task(plan_id, 'api'), self._task(plan_id, 'ui')
        self.assertIsNone(self.dana.service.waiting_for(ui))           # dana's host knows no plan yet
        self.assertIsNone(self.dana.service.follow_source('PROJ-404'))  # nothing published there
        followed = self.dana.service.follow_source(SOURCE, title='T')
        self.assertEqual(followed.plan_id, plan_id)
        self.assertEqual(self.dana.service.waiting_for(ui), [api])
        self.assertEqual(self.dana.service.waiting_for(api), [])
        self._merged(plan_id, 'api')
        self.dana.tick()
        self.assertEqual(self.dana.service.waiting_for(ui), [])
        self.tracker.read_error = RuntimeError('tracker down')
        with self.assertRaises(PlanQueueError) as caught:
            self.dana.service.follow_source(SOURCE)
        self.assertEqual(caught.exception.kind, ErrorKind.UNAVAILABLE)
        no_source = PlanQueueService(store=PlanQueueStore(tempfile.mkdtemp(prefix='plan-nosrc-')),
                                     proposer=FakeProposer(SPLIT), wording=FakeWording(),
                                     progress=self.tracker, probe=self.probe)
        self.assertIsNone(no_source.follow_source(SOURCE))

    def test_a_parent_without_a_task_is_waited_for_by_its_step_id(self) -> None:
        self.tracker.read_error = None
        plan = self.shay.service.propose(SOURCE, plan_markdown='# plan', repository_ids=['api', 'web'])
        self.assertTrue(wait_until(lambda: self.shay.plan(plan.plan_id).status == PlanStatus.PROPOSED))
        self.shay.launcher.is_available = True
        original = self.shay.launcher.prepare
        self.shay.launcher.prepare = lambda request: '' if request.step_id == 'api' else original(request)
        self.shay.service.approve(plan.plan_id)
        self.assertEqual(self.shay.service.waiting_for(self._task(plan.plan_id, 'ui')), ['api'])

    def test_the_operator_chooses_whether_steps_go_on_the_tracker(self) -> None:
        plan = self.shay.service.propose(SOURCE, plan_markdown='# plan', repository_ids=['api', 'web'])
        self.assertTrue(wait_until(lambda: self.shay.plan(plan.plan_id).status == PlanStatus.PROPOSED))
        self.assertTrue(self.shay.service.can_share(plan.plan_id))
        local = PlanQueueService(store=PlanQueueStore(tempfile.mkdtemp(prefix='plan-local-')),
                                 proposer=FakeProposer(SPLIT), wording=FakeWording(),
                                 progress=self.tracker, probe=self.probe)
        self.addCleanup(local.shutdown)
        local_plan = local.propose(SOURCE, plan_markdown='# plan', repository_ids=['api', 'web'])
        self.assertTrue(wait_until(lambda: local.get(local_plan.plan_id).status == PlanStatus.PROPOSED))
        self.assertFalse(local.can_share(local_plan.plan_id))
        with self.assertRaises(PlanQueueError) as caught:
            local.approve(local_plan.plan_id, shared=True)
        self.assertEqual(caught.exception.kind, ErrorKind.INVALID)
        self.assertIn('cannot hold its steps as sub-tasks', str(caught.exception))
        self.shay.service.bind_launcher(self.tracker.launcher('shay'))  # the host's own (local) tasks too
        kept = self.shay.service.approve(plan.plan_id, shared=False)
        self.assertEqual((kept.mode, kept.driver), (PlanMode.LOCAL, ''))
        self.assertIsNone(self.tracker.latest(SOURCE))  # nothing published for a plan kept here

    def test_count_as_done_needs_the_tracker(self) -> None:
        plan_id = self._approved()
        self.shay.service.bind_launcher(None, mode=PlanMode.TRACKER)
        with self.assertRaises(PlanQueueError) as caught:
            self.shay.service.mark_satisfied(plan_id, 'api')
        self.assertIn('its tracker cannot be reached from here', str(caught.exception))

    def test_count_as_done_is_marked_on_the_tracker_for_every_host(self) -> None:
        plan_id = self._followed(self._approved())
        api = self._task(plan_id, 'api')
        self.tracker.counted_errors[api] = RuntimeError('workflow forbids it')
        with self.assertRaises(PlanQueueError) as caught:
            self.shay.service.mark_satisfied(plan_id, 'api')
        self.assertEqual(caught.exception.kind, ErrorKind.UNAVAILABLE)
        self.assertIn(f'could not mark {api} done: workflow forbids it', str(caught.exception))
        self.assertNotEqual(self.shay.step(plan_id, 'api').status, StepStatus.SATISFIED)

        self.shay.service.mark_satisfied(plan_id, 'api')
        self.assertEqual(self.tracker.tasks[api]['state'], 'done')
        self.dana.tick()
        step = self.dana.step(plan_id, 'api')
        self.assertEqual((step.status, step.satisfied_by), (StepStatus.SATISFIED, SatisfiedBy.OPERATOR.value))

    # ----- taking the plan over -----

    def test_a_teammate_takes_the_whole_plan_over(self) -> None:
        plan_id = self._followed(self._approved())
        taken = self.dana.service.take_over(plan_id)
        self.assertEqual((taken.mode, taken.driver, taken.driver_user, taken.status),
                         (PlanMode.TRACKER, 'host-dana', 'dana', PlanStatus.PAUSED))
        self.assertIn('taken over by dana', self.dana.events)
        self.dana.tick()
        self.assertEqual((self.tracker.latest(SOURCE).author, self.tracker.latest(SOURCE).status), ('dana', 'paused'))
        self.shay.tick()
        mine = self.shay.plan(plan_id)
        self.assertEqual((mine.mode, mine.driver_user, mine.status), (PlanMode.FOLLOWED, 'dana', PlanStatus.PAUSED))
        self.assertIn('now driven by dana; status paused', self.shay.events)
        with self.assertRaises(PlanQueueError):
            self.shay.service.run(plan_id)
        self.dana.service.run(plan_id)
        self._merged(plan_id, 'api')
        self.dana.tick()
        self.assertEqual(self.tracker.tasks[self._task(plan_id, 'ui', self.dana)]['assignee'], 'dana')

    def test_take_over_refusals(self) -> None:
        plan_id = self._approved()
        with self.assertRaises(PlanQueueError) as caught:
            self.shay.service.take_over(plan_id)
        self.assertIn('not driven by another host', str(caught.exception))
        self._followed(plan_id)
        self.dana.ownership.error = RuntimeError('token expired')
        with self.assertRaises(PlanQueueError) as caught:
            self.dana.service.take_over(plan_id)
        self.assertEqual(caught.exception.kind, ErrorKind.UNAVAILABLE)
        self.dana.ownership.error = None
        self.shay.service.stop(plan_id)
        self.shay.tick()
        self.dana.tick()
        self.assertEqual(self.dana.plan(plan_id).status, PlanStatus.CANCELLED)
        with self.assertRaises(PlanQueueError) as caught:
            self.dana.service.take_over(plan_id)
        self.assertIn('the plan is cancelled', str(caught.exception))

    def test_taking_over_an_approved_plan_keeps_it_approved(self) -> None:
        plan_id = self._followed(self._approved(run=False))
        self.assertEqual(self.dana.service.take_over(plan_id).status, PlanStatus.APPROVED)

    # ----- records that are not followed -----

    def test_a_forged_record_or_one_that_rewrites_the_order_is_not_followed(self) -> None:
        plan_id = self._followed(self._approved())
        good = self.tracker.latest(SOURCE)
        forged = parse_record(render_record(self.shay.plan(plan_id)), author='mallory')
        self.tracker.post(SOURCE, forged)  # says "driven by shay", posted by mallory
        with self.assertLogs('PlanQueueService', 'WARNING') as logs:
            self.dana.tick()
        self.assertIn('posted by mallory, not by its driver shay', '\n'.join(logs.output))
        reordered = type(good)(
            plan_id=good.plan_id, source_task_id=SOURCE, status='running', driver='host-mallory',
            driver_user='mallory', author='mallory',
            steps=tuple(RecordStep(s.task_id, s.step_id, s.title, ()) for s in good.steps),
        )
        self.tracker.post(SOURCE, reordered)
        with self.assertLogs('PlanQueueService', 'WARNING') as logs:
            self.dana.tick()
        self.assertIn('changes which step waits for which', '\n'.join(logs.output))
        self.assertEqual(self.dana.plan(plan_id).driver_user, 'shay')

    def test_a_record_of_another_plan_or_an_unreadable_tracker_changes_nothing(self) -> None:
        plan_id = self._followed(self._approved())
        other = self.tracker.latest(SOURCE)
        self.tracker.post(SOURCE, type(other)(**{**other.__dict__, 'plan_id': 'f' * 32}))
        self.dana.tick()
        self.assertEqual(self.dana.plan(plan_id).driver_user, 'shay')
        self.tracker.read_error = RuntimeError('tracker down')
        with self.assertLogs('PlanQueueService', 'WARNING') as logs:
            self.dana.tick()
        self.assertIn('could not read the plan record of PROJ-1: tracker down', '\n'.join(logs.output))

    # ----- handed back, re-created, stopped -----

    def test_a_task_handed_back_is_released_again(self) -> None:
        plan_id = self._approved()
        api = self._task(plan_id, 'api')
        self.tracker.move(api, 'in_progress')
        self.shay.tick()
        self.assertEqual(self.shay.step(plan_id, 'api').status, StepStatus.ACTIVE)
        self.tracker.hand_back(api)
        self.shay.tick()
        self.assertEqual(self.tracker.tasks[api]['assignee'], 'shay')
        self.assertIn(f'{api}: ready', self.shay.events)
        self.assertNotEqual(self.shay.step(plan_id, 'api').reason, HANDED_BACK_REASON)  # released again

    def test_a_recreated_task_reaches_the_followers(self) -> None:
        plan_id = self._followed(self._approved())
        ui = self._task(plan_id, 'ui')
        self.tracker.delete(ui)
        self.shay.tick()
        self.assertEqual(self.shay.step(plan_id, 'ui').reason_code, 'task_gone')
        self.shay.service.retry_step(plan_id, 'ui')
        self.shay.tick()
        new_ui = self._task(plan_id, 'ui')
        self.assertNotEqual(new_ui, ui)
        self.dana.tick()
        step = self.dana.step(plan_id, 'ui')
        self.assertEqual((step.task_id, step.previous_task_ids), (new_ui, [ui]))

    def test_stopping_cancels_untaken_subtasks_and_followers_see_it(self) -> None:
        plan_id = self._followed(self._approved())
        ui, docs = self._task(plan_id, 'ui'), self._task(plan_id, 'docs')
        self.tracker.take(ui, 'dana')
        self.shay.tick()
        self.shay.service.stop(plan_id)
        self.assertEqual(self.tracker.cancelled, [docs])  # dana's ui is left alone
        self.shay.tick()
        self.dana.tick()
        self.assertEqual(self.dana.plan(plan_id).status, PlanStatus.CANCELLED)

    def test_discarding_a_followed_plan_forgets_it_here_only(self) -> None:
        plan_id = self._followed(self._approved())
        self.dana.service.discard(plan_id)
        self.assertEqual(self.dana.service.plans(), [])
        self.assertEqual(self.tracker.cancelled, [])
        self.assertEqual(self.shay.plan(plan_id).status, PlanStatus.RUNNING)

    def test_a_restart_never_moves_a_followed_plan(self) -> None:
        plan_id = self._followed(self._approved())
        self.dana.service.mark_interrupted('restarted')
        self.assertEqual(self.dana.plan(plan_id).status, PlanStatus.RUNNING)

    # ----- a host that can't publish or can't tell who it is -----

    def test_a_failed_publish_is_retried(self) -> None:
        self.tracker.publish_error = RuntimeError('comment rejected')
        plan_id = self._approved()
        plan = self.shay.plan(plan_id)
        self.assertLess(plan.published_version, plan.record_version)
        self.assertEqual(plan.record_retry_at, self.clock.now + PUBLISH_RETRY_SECONDS)
        self.tracker.publish_error = None
        self.shay.tick()
        self.assertIsNone(self.tracker.latest(SOURCE))  # not before the retry time
        self.clock.advance(PUBLISH_RETRY_SECONDS)
        self.shay.tick()
        self.assertEqual(self.tracker.latest(SOURCE).status, 'running')
        self.assertEqual(self.shay.plan(plan_id).record_retry_at, 0.0)

    def test_a_host_that_cannot_tell_who_it_is_moves_nothing(self) -> None:
        self.shay.ownership.error = RuntimeError('token expired')
        plan = self.shay.service.propose(SOURCE, plan_markdown='# plan', repository_ids=['api', 'web'])
        self.assertTrue(wait_until(lambda: self.shay.plan(plan.plan_id).status == PlanStatus.PROPOSED))
        with self.assertRaises(PlanQueueError) as caught:
            self.shay.service.approve(plan.plan_id)
        self.assertIn('cannot tell which tracker user this host is: token expired', str(caught.exception))
        self.shay.ownership.error = None
        self.shay.service.approve(plan.plan_id, run=True)
        self.shay.ownership.error = RuntimeError('token expired')
        with self.assertLogs('PlanQueueService', 'WARNING'):
            self.shay.tick()
        self.assertTrue(all(task['assignee'] == '' for task in self.tracker.tasks.values()))
        self.assertIsNone(self.tracker.latest(SOURCE))

    def test_a_host_with_no_way_to_drive_refuses_a_tracker_plan(self) -> None:
        host = PlanQueueService(
            store=PlanQueueStore(tempfile.mkdtemp(prefix='plan-nodrive-')), proposer=FakeProposer(SPLIT),
            wording=FakeWording(), progress=self.tracker, probe=self.probe, clock=self.clock,
            mode_for=lambda plan: PlanMode.TRACKER,
        )
        self.addCleanup(host.shutdown)
        host.bind_launcher(self.tracker.launcher('x'), mode=PlanMode.TRACKER)
        plan = host.propose(SOURCE, plan_markdown='# plan', repository_ids=['api', 'web'])
        self.assertTrue(wait_until(lambda: host.get(plan.plan_id).status == PlanStatus.PROPOSED))
        with self.assertRaises(PlanQueueError) as caught:
            host.approve(plan.plan_id)
        self.assertIn('cannot drive a shared plan', str(caught.exception))
        host.bind_launcher(None, mode=PlanMode.TRACKER)
        self.assertEqual(host.summaries()[0]['mode'], 'local')
        self.assertEqual(host._ticker.identity(), '')  # no ownership: never anyone's driver
        # A plan followed on a host with no way to read records again stays as it was.
        self.shay.service.approve(self._approved_plan_id(), run=True)
        self.shay.tick()
        followed = host.follow(self.tracker.latest('PROJ-50'))
        host.tick()
        self.assertEqual(host.get(followed.plan_id).mode, PlanMode.FOLLOWED)

    def _approved_plan_id(self) -> str:
        plan = self.shay.service.propose('PROJ-50', plan_markdown='# plan', repository_ids=['api', 'web'])
        self.assertTrue(wait_until(lambda: self.shay.plan(plan.plan_id).status == PlanStatus.PROPOSED))
        return plan.plan_id


if __name__ == '__main__':
    unittest.main()
