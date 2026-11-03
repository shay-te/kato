"""Changing an approved plan, and starting one of its tasks by hand.

What must hold:
- only tasks that have not started change: a started one stays exactly as it
  is and cannot be taken out, and a refused edit changes nothing;
- on a shared tracker the tasks follow the plan: an edited waiting task is
  rewritten, a new one is created (parents first), one taken out is withdrawn;
- a teammate's host following the plan takes the driver's edit, but never one
  from a record that also changes who drives it;
- Start hands over exactly one ready task, even while the plan is paused.
"""

from __future__ import annotations

import unittest

from plan_queue_core_lib.plan_queue_core_lib.data.plan import PlanStatus, StepStatus
from plan_queue_core_lib.plan_queue_core_lib.errors import ErrorKind, PlanQueueError
from plan_queue_core_lib.plan_queue_core_lib.graph import StepDraft
from plan_queue_core_lib.plan_queue_core_lib.tests.fakes import (
    FakeHistory,
    FakeProbe,
    FakeTracker,
    ManualClock,
)
from plan_queue_core_lib.plan_queue_core_lib.tests.test_shared_plans import SOURCE, Host


def _drafts(plan, **changes) -> list[StepDraft]:
    """Every step of ``plan`` as the editor sends it back, with ``changes``
    (step id -> dict of fields, or None to take the step out)."""
    drafts = []
    for step in plan.steps:
        change = changes.get(step.id, {})
        if change is None:
            continue
        drafts.append(StepDraft(
            id=step.id,
            title=change.get('title', step.title),
            brief=change.get('brief', step.brief),
            depends_on=tuple(change.get('depends_on', step.depends_on)),
            repositories=tuple(change.get('repositories', step.repositories)),
        ))
    return drafts


class _LivePlan(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = ManualClock(1_000_000.0)
        self.probe = FakeProbe()
        self.history = FakeHistory()
        self.tracker = FakeTracker(self.clock)
        self.shay = Host(self, self.tracker, identity='host-shay', user='shay')
        self.dana = Host(self, self.tracker, identity='host-dana', user='dana')

    def _approved(self, *, run: bool = False) -> str:
        from plan_queue_core_lib.plan_queue_core_lib.tests.fakes import wait_until
        plan = self.shay.service.propose(SOURCE, plan_markdown='# plan', repository_ids=['api', 'web'], title='T')
        self.assertTrue(wait_until(lambda: self.shay.plan(plan.plan_id).status == PlanStatus.PROPOSED))
        self.shay.service.approve(plan.plan_id, run=run, shared=True)
        self.shay.tick()
        return plan.plan_id

    def _edit(self, plan_id, **changes):
        return self.shay.service.edit(plan_id, _drafts(self.shay.plan(plan_id), **changes))

    def _task(self, plan_id, step_id) -> str:
        return self.shay.step(plan_id, step_id).task_id

    def _unchanged_on_refusal(self, plan_id, kind, words, **changes) -> None:
        before = self.shay.plan(plan_id).to_dict()
        with self.assertRaises(PlanQueueError) as caught:
            self._edit(plan_id, **changes)
        self.assertEqual(caught.exception.kind, kind)
        self.assertIn(words, str(caught.exception))
        self.assertEqual(self.shay.plan(plan_id).to_dict(), before)


class EditWaitingTasksTests(_LivePlan):
    def test_a_waiting_task_s_text_changes_here_and_on_its_ticket(self) -> None:
        plan_id = self._approved()
        docs = self._task(plan_id, 'docs')
        self._edit(plan_id, docs={'title': 'Write the guide', 'brief': 'Cover the new endpoint.'})
        step = self.shay.step(plan_id, 'docs')
        self.assertEqual((step.title, step.brief), ('Write the guide', 'Cover the new endpoint.'))
        self.assertEqual(self.tracker.tasks[docs]['title'], 'Write the guide')
        self.assertEqual(step.task_id, docs)  # the same ticket, rewritten

    def test_what_a_task_waits_for_changes_and_its_ticket_says_so(self) -> None:
        plan_id = self._approved()
        docs = self._task(plan_id, 'docs')
        self._edit(plan_id, docs={'depends_on': ()})
        self.assertEqual(self.shay.step(plan_id, 'docs').status, StepStatus.READY)
        self.assertEqual(self.tracker.tasks[docs]['waits_for'], ())

    def test_a_new_task_gets_its_ticket_after_its_parent(self) -> None:
        plan_id = self._approved()
        plan = self.shay.plan(plan_id)
        drafts = _drafts(plan) + [
            StepDraft('check', 'Check it', 'Check the guide.', depends_on=('guide',)),
            StepDraft('guide', 'Write a guide', 'Write it.', depends_on=('api',)),
        ]
        self.shay.service.edit(plan_id, drafts)
        guide, check = self._task(plan_id, 'guide'), self._task(plan_id, 'check')
        self.assertTrue(guide and check)
        self.assertEqual(self.tracker.tasks[check]['waits_for'], (guide,))  # its parent's ticket existed first
        self.assertEqual(self.tracker.tasks[guide]['assignee'], '')        # created unassigned, like the rest
        self.assertEqual(self.shay.step(plan_id, 'check').status, StepStatus.BLOCKED)

    def test_a_waiting_task_that_comes_to_wait_for_a_new_one_names_its_ticket(self) -> None:
        plan_id = self._approved()
        docs = self._task(plan_id, 'docs')
        drafts = _drafts(self.shay.plan(plan_id), docs={'depends_on': ('api', 'spec')}) + [
            StepDraft('spec', 'Spec', 'Write the spec.'),
        ]
        self.shay.service.edit(plan_id, drafts)
        api, spec = self._task(plan_id, 'api'), self._task(plan_id, 'spec')
        self.assertEqual(self.tracker.tasks[docs]['waits_for'], (api, spec))

    def test_a_task_taken_out_keeps_its_ticket_withdrawn(self) -> None:
        plan_id = self._approved()
        docs = self._task(plan_id, 'docs')
        self._edit(plan_id, docs=None)
        self.assertIsNone(self.shay.step(plan_id, 'docs'))
        self.assertIn(docs, self.tracker.cancelled)
        self.assertIn(docs, self.tracker.tasks)  # never deleted

    def test_the_record_follows_the_edit(self) -> None:
        plan_id = self._approved()
        self._edit(plan_id, docs={'depends_on': ()})
        self.shay.tick()
        record = self.tracker.latest(SOURCE)
        self.assertEqual({s.step_id: s.waits_for for s in record.steps}['docs'], ())

    def test_an_edit_that_changes_nothing_rewrites_nothing(self) -> None:
        plan_id = self._approved()
        before = {task_id: dict(task) for task_id, task in self.tracker.tasks.items()}
        self._edit(plan_id)
        self.assertEqual(self.tracker.tasks, before)


class StartedTasksStayTests(_LivePlan):
    def _started(self) -> str:
        plan_id = self._approved(run=True)
        self.assertEqual(self.shay.step(plan_id, 'api').status, StepStatus.STARTING)
        return plan_id

    def test_a_started_task_cannot_change(self) -> None:
        plan_id = self._started()
        self._unchanged_on_refusal(plan_id, ErrorKind.CONFLICT, 'has already started', api={'title': 'Other'})

    def test_a_started_task_cannot_be_taken_out(self) -> None:
        plan_id = self._started()
        self._unchanged_on_refusal(
            plan_id, ErrorKind.CONFLICT, 'has already started',
            api=None, ui={'depends_on': ()}, docs={'depends_on': ()},
        )

    def test_waiting_tasks_change_beside_an_unchanged_started_one(self) -> None:
        plan_id = self._started()
        self._edit(plan_id, ui={'title': 'Use it in the page'})
        self.assertEqual(self.shay.step(plan_id, 'ui').title, 'Use it in the page')
        self.assertEqual(self.shay.step(plan_id, 'api').status, StepStatus.STARTING)

    def test_a_task_someone_took_early_counts_as_started(self) -> None:
        plan_id = self._approved()
        self.tracker.take(self._task(plan_id, 'docs'), 'dana')
        self.shay.tick()
        self._unchanged_on_refusal(plan_id, ErrorKind.CONFLICT, 'has already started', docs={'title': 'X'})

    def test_a_split_the_validator_refuses_changes_nothing(self) -> None:
        plan_id = self._approved()
        self._unchanged_on_refusal(
            plan_id, ErrorKind.INVALID, 'in a circle', api={'depends_on': ('docs',)},
        )

    def test_a_plan_another_host_drives_is_not_edited_here(self) -> None:
        plan_id = self._approved()
        followed = self.dana.service.follow(self.tracker.latest(SOURCE), title='T')
        with self.assertRaises(PlanQueueError) as caught:
            self.dana.service.edit(plan_id, _drafts(followed, docs={'title': 'X'}))
        self.assertIn('take it over to change it', str(caught.exception))


class TicketRewriteFailureTests(_LivePlan):
    def test_a_ticket_that_cannot_be_rewritten_is_named_and_a_second_save_retries(self) -> None:
        plan_id = self._approved()
        docs = self._task(plan_id, 'docs')
        real = self.shay.launcher.update

        def refuse(request, task_id):
            raise RuntimeError('tracker down')

        self.shay.launcher.update = refuse
        with self.assertLogs('PlanQueueService', 'ERROR'):
            with self.assertRaises(PlanQueueError) as caught:
                self._edit(plan_id, docs={'title': 'Write the guide'})
        self.assertEqual(caught.exception.kind, ErrorKind.UNAVAILABLE)
        self.assertIn(f'{docs}: tracker down', str(caught.exception))
        self.assertEqual(self.shay.step(plan_id, 'docs').title, 'Write the guide')  # the plan holds the edit
        self.shay.launcher.update = real
        self._edit(plan_id, docs={'title': 'Write the guide', 'brief': 'Again.'})
        self.assertEqual(self.tracker.tasks[docs]['title'], 'Write the guide')


class FollowersTakeTheEditTests(_LivePlan):
    def _followed(self, plan_id) -> None:
        self.dana.service.follow(self.tracker.latest(SOURCE), title='T')

    def test_a_follower_rewires_adds_and_drops_as_the_driver_did(self) -> None:
        plan_id = self._approved()
        self._followed(plan_id)
        docs = self._task(plan_id, 'docs')
        drafts = _drafts(self.shay.plan(plan_id), docs=None, ui={'depends_on': ()}) + [
            StepDraft('check', 'Check it', 'Check.', depends_on=('ui',)),
        ]
        self.shay.service.edit(plan_id, drafts)
        self.shay.tick()
        self.dana.tick()
        followed = self.dana.plan(plan_id)
        self.assertEqual([step.id for step in followed.steps], ['api', 'ui', 'check'])
        ui, check = self._task(plan_id, 'ui'), self._task(plan_id, 'check')
        self.assertEqual(self.dana.service.waiting_for(ui), [])
        self.assertEqual(self.dana.service.waiting_for(check), [ui])
        self.assertIsNone(self.dana.service.waiting_for(docs))
        self.assertIn(f'{check} joined the plan', '\n'.join(self.dana.events))

    def test_a_follower_keeps_a_done_task_the_driver_cannot_drop(self) -> None:
        plan_id = self._approved(run=True)
        self._followed(plan_id)
        api = self._task(plan_id, 'api')
        self.tracker.move(api, 'done')
        from plan_queue_core_lib.plan_queue_core_lib.ports import PullRequestFact
        from plan_queue_core_lib.plan_queue_core_lib.tests.test_shared_plans import iso
        self.probe.set(api, 'api', PullRequestFact('merged', 'https://git/x', iso(self.tracker.tasks[api]['created'] + 1)))
        self.shay.tick()
        self.shay.tick()
        self.assertEqual(self.shay.step(plan_id, 'api').status, StepStatus.SATISFIED)
        with self.assertRaises(PlanQueueError):
            self._edit(plan_id, api=None, ui={'depends_on': ()}, docs={'depends_on': ()})


class StartOneTaskTests(_LivePlan):
    def test_start_hands_over_one_ready_task_while_the_plan_waits_for_run(self) -> None:
        plan_id = self._approved()
        self.assertEqual(self.shay.plan(plan_id).status, PlanStatus.APPROVED)
        self.shay.service.start_step(plan_id, 'api')
        api = self._task(plan_id, 'api')
        self.assertEqual(self.tracker.tasks[api]['assignee'], 'shay')
        self.assertEqual(self.shay.step(plan_id, 'api').status, StepStatus.STARTING)
        self.assertEqual(self.shay.plan(plan_id).status, PlanStatus.APPROVED)  # Run was not pressed

    def test_start_works_while_paused(self) -> None:
        plan_id = self._approved(run=True)
        self.shay.service.pause(plan_id)
        self._edit(plan_id, docs={'depends_on': ()})
        self.shay.service.start_step(plan_id, 'docs')
        self.assertEqual(self.tracker.tasks[self._task(plan_id, 'docs')]['assignee'], 'shay')

    def test_a_task_that_still_waits_cannot_be_started(self) -> None:
        plan_id = self._approved()
        with self.assertRaises(PlanQueueError) as caught:
            self.shay.service.start_step(plan_id, 'ui')
        self.assertIn('still waits for another task', str(caught.exception))
        self.assertEqual(self.tracker.tasks[self._task(plan_id, 'ui')]['assignee'], '')

    def test_only_the_driver_starts_a_task(self) -> None:
        plan_id = self._approved()
        self.dana.service.follow(self.tracker.latest(SOURCE), title='T')
        with self.assertRaises(PlanQueueError) as caught:
            self.dana.service.start_step(plan_id, 'api')
        self.assertIn('take it over to change it', str(caught.exception))

    def test_a_finished_plan_starts_nothing(self) -> None:
        plan_id = self._approved()
        self.shay.service.stop(plan_id)
        with self.assertRaises(PlanQueueError):
            self.shay.service.start_step(plan_id, 'api')


class TaskStateTests(_LivePlan):
    def test_the_card_carries_the_ticket_s_own_state(self) -> None:
        plan_id = self._approved(run=True)
        api = self._task(plan_id, 'api')
        self.assertEqual(self.shay.step(plan_id, 'api').task_state, 'todo')
        self.tracker.move(api, 'in_review')
        self.shay.tick()
        self.assertEqual(self.shay.step(plan_id, 'api').task_state, 'in_review')
        self.assertEqual(self.shay.step(plan_id, 'api').to_dict()['task_state'], 'in_review')


if __name__ == '__main__':
    unittest.main()
