"""A plan's data survives a round trip, and an odd saved file still loads."""

from __future__ import annotations

import unittest

from plan_queue_core_lib.plan_queue_core_lib.data.plan import (
    MergeCheck,
    Plan,
    PlanStatus,
    RepoMerge,
    RepoMergeState,
    Revision,
    Step,
    StepStatus,
)


def _full_plan() -> Plan:
    plan = Plan.new('SRC-1', plan_markdown='# Plan\nDo it.', repository_ids=['api', 'web'],
                    now=100.0, title='Discount codes', source_summary='summary', model='m1')
    plan.status = PlanStatus.RUNNING
    plan.reason = 'why'
    plan.notes = 'notes'
    plan.start_mode = 'implement'
    plan.step_model = 'm2'
    plan.effort = 'high'
    plan.revision = 2
    plan.revisions = [Revision(1, 'smaller', 50.0)]
    plan.approved_at = 120.0
    plan.steps = [
        Step(id='a', title='A', brief='do a', repositories=['api'], status=StepStatus.SATISFIED,
             task_id='T-1', previous_task_ids=['T-0'], released_at=130.0,
             satisfied_by='merged', satisfied_at=140.0,
             merge=MergeCheck(checked_at=1.0, next_check_at=2.0, attempts=3, forced=True,
                              repos=[RepoMerge('api', RepoMergeState.MERGED, 'u')])),
        Step(id='b', title='B', brief='do b', depends_on=['a'], repositories=['web'],
             status=StepStatus.FAILED, reason_code='pr_declined', reason='closed'),
    ]
    return plan


class PlanDataTests(unittest.TestCase):
    def test_round_trip_keeps_every_field(self) -> None:
        plan = _full_plan()
        self.assertEqual(Plan.from_dict(plan.to_dict()).to_dict(), plan.to_dict())

    def test_new_plan_has_an_id_a_digest_and_falls_back_for_its_title(self) -> None:
        plan = Plan.new('SRC-1', plan_markdown='x', repository_ids=('r',), now=5.0)
        self.assertRegex(plan.plan_id, r'^[0-9a-f]{32}$')
        self.assertEqual(len(plan.plan_digest), 64)
        self.assertEqual(plan.title, 'SRC-1')
        self.assertEqual(plan.status, PlanStatus.PROPOSING)
        self.assertEqual((plan.created_at, plan.updated_at), (5.0, 5.0))
        self.assertEqual(Plan.new('S', plan_markdown='x', repository_ids=[], now=0,
                                  source_summary='sum').title, 'sum')

    def test_an_empty_or_odd_record_loads_with_safe_defaults(self) -> None:
        plan = Plan.from_dict({
            'plan_id': 'p', 'status': 'not-a-status', 'revision': 'x', 'created_at': 'later',
            'repository_ids': ['ok', 3, ''], 'steps': [{'id': 's', 'status': 'weird',
                                                         'merge': 'not a dict',
                                                         'depends_on': 'a'}, 'junk'],
            'revisions': ['junk', {'number': 'two'}],
        })
        self.assertEqual(plan.status, PlanStatus.PROPOSE_FAILED)
        self.assertEqual(plan.revision, 0)
        self.assertEqual(plan.created_at, 0.0)
        self.assertEqual(plan.repository_ids, ['ok'])
        self.assertEqual(len(plan.steps), 1)
        self.assertEqual(plan.steps[0].status, StepStatus.BLOCKED)
        self.assertEqual(plan.steps[0].depends_on, [])
        self.assertEqual(plan.steps[0].merge.to_dict(), MergeCheck().to_dict())
        self.assertEqual(plan.revisions[0].number, 0)
        self.assertEqual(Plan.from_dict({}).steps, [])

    def test_merge_check_tolerates_a_bad_record(self) -> None:
        check = MergeCheck.from_dict({'attempts': -4, 'forced': 'yes', 'repos': [{'state': '??'}]})
        self.assertEqual(check.attempts, 0)
        self.assertFalse(check.forced)
        self.assertEqual(check.repos[0].state, RepoMergeState.UNKNOWN)
        self.assertEqual(MergeCheck.from_dict(None).repos, [])

    def test_lookups_and_counts(self) -> None:
        plan = _full_plan()
        self.assertEqual(plan.step('b').title, 'B')
        self.assertIsNone(plan.step('zz'))
        self.assertEqual(plan.index_of('b'), 1)
        self.assertEqual(plan.index_of('zz'), -1)
        counts = plan.status_counts()
        self.assertEqual(counts['satisfied'], 1)
        self.assertEqual(counts['failed'], 1)
        self.assertEqual(sum(counts.values()), 2)

    def test_status_properties(self) -> None:
        self.assertTrue(PlanStatus.FINISHED.is_terminal)
        self.assertTrue(PlanStatus.CANCELLED.is_terminal)
        self.assertFalse(PlanStatus.RUNNING.is_terminal)
        self.assertTrue(PlanStatus.PAUSED.has_steps_in_flight)
        self.assertFalse(PlanStatus.PROPOSED.has_steps_in_flight)
        self.assertTrue(StepStatus.AWAITING_MERGE.is_released)
        self.assertFalse(StepStatus.READY.is_released)
        self.assertTrue(StepStatus.CANCELLED.is_terminal)
        self.assertFalse(StepStatus.FAILED.is_terminal)

    def test_a_step_prepared_but_never_released(self) -> None:
        step = Step(id='s', title='t', brief='b', task_id='T-1')
        self.assertTrue(step.prepared_only)
        step.released_at = 9.0
        self.assertFalse(step.prepared_only)
        self.assertFalse(Step(id='s', title='t', brief='b').prepared_only)
        self.assertFalse(Step(id='s', title='t', brief='b', task_id='T-1',
                              status=StepStatus.ACTIVE).prepared_only)
        self.assertFalse(Step(id='s', title='t', brief='b').is_satisfied)


if __name__ == '__main__':
    unittest.main()
