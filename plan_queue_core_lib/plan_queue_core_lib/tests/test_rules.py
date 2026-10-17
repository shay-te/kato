"""The pure rules: merge verdicts, progress from observations, release order."""

from __future__ import annotations

import unittest

from plan_queue_core_lib.plan_queue_core_lib.data.plan import (
    Plan,
    RepoMerge,
    RepoMergeState,
    Step,
    StepStatus,
)
from plan_queue_core_lib.plan_queue_core_lib.merge import (
    CLOCK_SKEW_SECONDS,
    PROBE_BACKOFF_SECONDS,
    MergeVerdict,
    classify_repository,
    found_nothing,
    merge_verdict,
    next_probe_delay,
)
from plan_queue_core_lib.plan_queue_core_lib.ports import PullRequestFact, TaskObservation, TaskPhase
from plan_queue_core_lib.plan_queue_core_lib.progress import (
    HANDED_BACK_REASON,
    UNRELEASED_GRACE_SECONDS,
    change_from_observation,
)
from plan_queue_core_lib.plan_queue_core_lib.release import (
    blocked_reason,
    initial_status,
    is_finished,
    releasable,
    unblocked,
    waiting_on,
)

RELEASED_AT = 1_700_000_000.0  # 2023-11-14T22:13:20Z
BEFORE = '2023-11-14T21:00:00Z'
AFTER = '2023-11-14T23:00:00+00:00'


class ClassifyRepositoryTests(unittest.TestCase):
    def _state(self, *facts, released_at=RELEASED_AT):
        return classify_repository('api', facts, since=released_at)

    def test_open_wins_then_merged_then_declined(self) -> None:
        merged = PullRequestFact('merged', 'u-m', AFTER)
        opened = PullRequestFact('open', 'u-o', AFTER)
        declined = PullRequestFact('declined', 'u-d', AFTER)
        self.assertEqual(self._state(merged, opened).state, RepoMergeState.OPEN)
        self.assertEqual(self._state(merged, opened).url, 'u-o')
        self.assertEqual(self._state(declined, merged).state, RepoMergeState.MERGED)
        self.assertEqual(self._state(declined, merged).url, 'u-m')
        self.assertEqual(self._state(declined), RepoMerge('api', RepoMergeState.DECLINED, 'u-d'))
        self.assertEqual(self._state(), RepoMerge('api', RepoMergeState.NONE))

    def test_a_request_older_than_the_release_is_ignored(self) -> None:
        old_merge = PullRequestFact('merged', 'old', BEFORE)
        self.assertEqual(self._state(old_merge).state, RepoMergeState.NONE)

    def test_clock_skew_is_tolerated(self) -> None:
        skewed = PullRequestFact('merged', 'u', '2023-11-14T22:12:00Z')  # 80s "before"
        self.assertLess(80, CLOCK_SKEW_SECONDS)
        self.assertEqual(self._state(skewed).state, RepoMergeState.MERGED)

    def test_without_a_time_to_compare_a_request_is_kept(self) -> None:
        for fact in (PullRequestFact('merged', 'u', ''), PullRequestFact('merged', 'u', 'garbage')):
            self.assertEqual(self._state(fact).state, RepoMergeState.MERGED)
        self.assertEqual(self._state(PullRequestFact('merged', 'u', BEFORE), released_at=0).state,
                         RepoMergeState.MERGED)

    def test_a_merged_request_without_a_url_still_reports_any_url(self) -> None:
        state = self._state(PullRequestFact('merged', '', AFTER), PullRequestFact('declined', 'u-d', AFTER))
        self.assertEqual((state.state, state.url), (RepoMergeState.MERGED, 'u-d'))


class MergeVerdictTests(unittest.TestCase):
    def _v(self, *states):
        return merge_verdict([RepoMerge(f'r{i}', state) for i, state in enumerate(states)])

    def test_truth_table(self) -> None:
        S = RepoMergeState
        self.assertEqual(self._v(), MergeVerdict.WAITING)
        self.assertEqual(self._v(S.MERGED, S.UNKNOWN), MergeVerdict.WAITING)
        self.assertEqual(self._v(S.MERGED, S.OPEN), MergeVerdict.WAITING)
        self.assertEqual(self._v(S.OPEN, S.DECLINED), MergeVerdict.WAITING)
        self.assertEqual(self._v(S.MERGED, S.DECLINED), MergeVerdict.DECLINED)
        self.assertEqual(self._v(S.MERGED, S.NONE), MergeVerdict.MERGED)
        self.assertEqual(self._v(S.MERGED), MergeVerdict.MERGED)
        self.assertEqual(self._v(S.NONE, S.NONE), MergeVerdict.WAITING)

    def test_backoff_grows_then_holds(self) -> None:
        self.assertEqual(next_probe_delay(-3), PROBE_BACKOFF_SECONDS[0])
        self.assertEqual(next_probe_delay(1), PROBE_BACKOFF_SECONDS[1])
        self.assertEqual(next_probe_delay(99), PROBE_BACKOFF_SECONDS[-1])

    def test_found_nothing(self) -> None:
        self.assertTrue(found_nothing([RepoMerge('a', RepoMergeState.NONE)]))
        self.assertFalse(found_nothing([]))
        self.assertFalse(found_nothing([RepoMerge('a', RepoMergeState.NONE),
                                        RepoMerge('b', RepoMergeState.UNKNOWN)]))


class ProgressTests(unittest.TestCase):
    NOW = 10_000.0

    def _change(self, status, phase, *, released_at=NOW - 10, detail='', **seen):
        step = Step(id='s', title='t', brief='b', status=status, task_id='T-1', released_at=released_at)
        return change_from_observation(step, TaskObservation(phase, detail, **seen), now=self.NOW)

    def test_an_unreleased_step_moves_only_when_someone_took_its_task(self) -> None:
        # A tracker sub-task waiting its turn, unassigned: nothing happens.
        self.assertIsNone(self._change(StepStatus.READY, TaskPhase.UNRELEASED, released_at=0))
        self.assertIsNone(self._change(StepStatus.BLOCKED, TaskPhase.WAITING, released_at=0))
        self.assertIsNone(self._change(StepStatus.READY, TaskPhase.PREPARING, released_at=0))
        # Someone took it before its turn: the board follows them.
        taken = self._change(StepStatus.BLOCKED, TaskPhase.WAITING, released_at=0, assignee='dana')
        self.assertEqual((taken.status, taken.reason, taken.taken),
                         (StepStatus.STARTING, 'taken before its turn', True))
        for phase, status in ((TaskPhase.IN_PROGRESS, StepStatus.ACTIVE), (TaskPhase.IN_REVIEW, StepStatus.IN_REVIEW),
                              (TaskPhase.DONE, StepStatus.AWAITING_MERGE)):
            change = self._change(StepStatus.READY, phase, released_at=0)
            self.assertEqual((change.status, change.reason, change.taken), (status, '', True))

    def test_a_task_counted_done_by_the_driver_satisfies_the_step(self) -> None:
        for status in (StepStatus.AWAITING_MERGE, StepStatus.ACTIVE, StepStatus.BLOCKED):
            change = self._change(status, TaskPhase.DONE, counted_done=True)
            self.assertEqual((change.status, change.satisfied_by), (StepStatus.SATISFIED, 'operator'))
        self.assertTrue(self._change(StepStatus.BLOCKED, TaskPhase.DONE, counted_done=True).taken)

    def test_a_finished_or_failed_step_never_moves(self) -> None:
        for status in (StepStatus.SATISFIED, StepStatus.CANCELLED, StepStatus.FAILED):
            self.assertIsNone(self._change(status, TaskPhase.GONE))

    def test_a_removed_task_fails_the_step(self) -> None:
        for phase in (TaskPhase.MISSING, TaskPhase.GONE):
            change = self._change(StepStatus.AWAITING_MERGE, phase)
            self.assertEqual((change.status, change.reason_code), (StepStatus.FAILED, 'task_gone'))

    def test_waiting_for_the_merge_ignores_everything_else(self) -> None:
        self.assertIsNone(self._change(StepStatus.AWAITING_MERGE, TaskPhase.IN_PROGRESS))

    def test_phases_map_to_statuses(self) -> None:
        self.assertEqual(self._change(StepStatus.STARTING, TaskPhase.PREPARING), None)
        self.assertEqual(self._change(StepStatus.STARTING, TaskPhase.IN_PROGRESS).status, StepStatus.ACTIVE)
        self.assertEqual(self._change(StepStatus.ACTIVE, TaskPhase.IN_REVIEW).status, StepStatus.IN_REVIEW)
        self.assertEqual(self._change(StepStatus.IN_REVIEW, TaskPhase.DONE).status, StepStatus.AWAITING_MERGE)
        self.assertEqual(self._change(StepStatus.ACTIVE, TaskPhase.WAITING).status, StepStatus.STARTING)

    def test_an_unknown_phase_changes_nothing(self) -> None:
        step = Step(id='s', title='t', brief='b', status=StepStatus.ACTIVE, task_id='T-1')
        odd = TaskObservation.__new__(TaskObservation)
        object.__setattr__(odd, 'phase', 'not-a-phase')
        object.__setattr__(odd, 'detail', '')
        self.assertIsNone(change_from_observation(step, odd, now=self.NOW))

    def test_an_errored_run(self) -> None:
        starting = self._change(StepStatus.STARTING, TaskPhase.ERRORED, detail='clone failed')
        self.assertEqual((starting.status, starting.reason_code, starting.reason),
                         (StepStatus.FAILED, 'launch_failed', 'clone failed'))
        self.assertEqual(self._change(StepStatus.STARTING, TaskPhase.ERRORED).reason,
                         'T-1 could not be started')
        active = self._change(StepStatus.ACTIVE, TaskPhase.ERRORED)
        self.assertEqual((active.status, active.reason), (StepStatus.ACTIVE, 'its run hit an error'))

    def test_a_release_that_never_landed_is_retried_after_the_grace(self) -> None:
        self.assertIsNone(self._change(StepStatus.STARTING, TaskPhase.UNRELEASED, released_at=self.NOW - 5))
        late = self._change(StepStatus.STARTING, TaskPhase.UNRELEASED,
                            released_at=self.NOW - UNRELEASED_GRACE_SECONDS)
        self.assertEqual(late.status, StepStatus.READY)

    def test_a_task_handed_back_returns_the_step_to_the_queue(self) -> None:
        for status in (StepStatus.ACTIVE, StepStatus.IN_REVIEW):
            back = self._change(status, TaskPhase.UNRELEASED, released_at=self.NOW - 5)
            self.assertEqual((back.status, back.reason), (StepStatus.READY, HANDED_BACK_REASON))


class ReleaseRuleTests(unittest.TestCase):
    def _plan(self) -> Plan:
        plan = Plan.new('S', plan_markdown='x', repository_ids=['r'], now=0)
        plan.steps = [
            Step(id='a', title='A', brief='b', status=StepStatus.SATISFIED, task_id='T-1'),
            Step(id='b', title='B', brief='b', status=StepStatus.ACTIVE),
            Step(id='c', title='C', brief='b', depends_on=['a'], status=StepStatus.BLOCKED),
            Step(id='d', title='D', brief='b', depends_on=['a', 'b', 'zz'], status=StepStatus.BLOCKED),
            Step(id='e', title='E', brief='b', status=StepStatus.READY),
        ]
        return plan

    def test_waiting_and_unblocking(self) -> None:
        plan = self._plan()
        self.assertEqual(waiting_on(plan, plan.step('d')), ['b', 'zz'])
        self.assertEqual([step.id for step in unblocked(plan)], ['c'])
        self.assertEqual([step.id for step in releasable(plan)], ['e'])
        self.assertEqual(initial_status(plan, plan.step('c')), StepStatus.READY)
        self.assertEqual(initial_status(plan, plan.step('d')), StepStatus.BLOCKED)
        self.assertEqual(blocked_reason(plan, plan.step('d')), 'waits for B, zz')
        self.assertEqual(blocked_reason(plan, plan.step('c')), '')

    def test_a_parent_with_a_task_is_named_by_it(self) -> None:
        plan = self._plan()
        plan.step('b').task_id = 'T-7'
        self.assertEqual(blocked_reason(plan, plan.step('d')), 'waits for T-7, zz')

    def test_finished(self) -> None:
        plan = self._plan()
        self.assertFalse(is_finished(plan))
        for step in plan.steps:
            step.status = StepStatus.SATISFIED
        plan.steps[0].status = StepStatus.CANCELLED
        self.assertTrue(is_finished(plan))
        plan.steps = []
        self.assertFalse(is_finished(plan))


if __name__ == '__main__':
    unittest.main()
