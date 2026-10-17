"""Proposing a split: a good reply lands, a bad one is repaired once, and a
result the plan no longer waits for is dropped."""

from __future__ import annotations

import tempfile
import threading
import unittest
from unittest import mock

from plan_queue_core_lib.plan_queue_core_lib.data.plan import Plan, PlanStatus, Step
from plan_queue_core_lib.plan_queue_core_lib.proposing import ProposalWorker
from plan_queue_core_lib.plan_queue_core_lib.registry import PlanRegistry
from plan_queue_core_lib.plan_queue_core_lib.store import PlanQueueStore
from plan_queue_core_lib.plan_queue_core_lib.tests.fakes import (
    FakeProposer,
    FakeWording,
    ManualClock,
    split_reply,
    step_spec,
    wait_until,
)

GOOD = split_reply([step_spec('api'), step_spec('ui', depends_on=['api'])], notes='two')
BAD = split_reply([step_spec('ui', depends_on=['nope'])])


class ProposingTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix='plan-proposing-')
        self.addCleanup(tmp.cleanup)
        self.store = PlanQueueStore(tmp.name)
        self.registry = PlanRegistry(self.store, clock=ManualClock())
        self.events: list[str] = []

    def _worker(self, proposer) -> ProposalWorker:
        return ProposalWorker(
            self.registry, proposer, FakeWording(),
            observer=lambda plan, event: self.events.append(event),
        )

    def _plan(self, **changes) -> Plan:
        plan = Plan.new('SRC-1', plan_markdown='THE PLAN', repository_ids=['api', 'web'],
                        now=1.0, model='opus')
        for key, value in changes.items():
            setattr(plan, key, value)
        return self.registry.add(plan)

    def _run(self, worker, plan, feedback='') -> Plan:
        worker.start(plan, feedback=feedback)
        worker.join(plan.plan_id, timeout=5)
        return self.registry.get(plan.plan_id)

    def test_a_good_reply_becomes_the_proposed_steps(self) -> None:
        proposer = FakeProposer(GOOD)
        plan = self._run(self._worker(proposer), self._plan())
        self.assertEqual(plan.status, PlanStatus.PROPOSED)
        self.assertEqual([step.id for step in plan.steps], ['api', 'ui'])
        self.assertEqual(plan.notes, 'two')
        self.assertEqual(self.events, ['proposed 2 steps'])
        prompt = proposer.prompts[0]
        self.assertIn('<untrusted source="plan of SRC-1">\nTHE PLAN', prompt)
        self.assertIn('Host guidance', prompt)
        self.assertEqual(proposer.calls[0], {'source_task_id': 'SRC-1', 'model': 'opus'})
        self.assertTrue((self.store._path(plan.plan_id, '.reply.txt')).is_file())

    def test_a_bad_reply_is_repaired_once(self) -> None:
        proposer = FakeProposer(BAD, GOOD)
        plan = self._run(self._worker(proposer), self._plan())
        self.assertEqual(plan.status, PlanStatus.PROPOSED)
        self.assertIn("unknown step 'nope'", proposer.prompts[1])

    def test_two_bad_replies_fail_the_proposal_with_the_reason(self) -> None:
        plan = self._run(self._worker(FakeProposer(BAD)), self._plan())
        self.assertEqual(plan.status, PlanStatus.PROPOSE_FAILED)
        self.assertIn("unknown step 'nope'", plan.reason)
        self.assertTrue(self.events[0].startswith('the split proposal failed'))

    def test_a_proposer_error_fails_the_proposal(self) -> None:
        plan = self._run(self._worker(FakeProposer(error=TimeoutError('took too long'))), self._plan())
        self.assertEqual((plan.status, plan.reason),
                         (PlanStatus.PROPOSE_FAILED, 'the proposer failed: took too long'))

    def test_an_unexpected_crash_is_recorded_not_lost(self) -> None:
        worker = self._worker(FakeProposer(GOOD))
        with mock.patch.object(worker, '_propose', side_effect=KeyError('boom')), \
                self.assertLogs('ProposalWorker', level='ERROR'):
            plan = self._run(worker, self._plan())
        self.assertEqual(plan.status, PlanStatus.PROPOSE_FAILED)
        self.assertIn('boom', plan.reason)

    def test_a_revision_sends_the_previous_split_and_the_feedback(self) -> None:
        proposer = FakeProposer(GOOD)
        previous = [Step(id='old', title='Old', brief='old brief', repositories=['api'])]
        self._run(self._worker(proposer), self._plan(steps=previous), feedback='fewer steps')
        self.assertIn('Your previous split was:', proposer.prompts[0])
        self.assertIn('"old"', proposer.prompts[0])
        self.assertIn('fewer steps', proposer.prompts[0])

    def test_cancelling_drops_the_result(self) -> None:
        gate = threading.Event()
        worker = self._worker(FakeProposer(GOOD, gate=gate))
        plan = self._plan()
        worker.start(plan)
        self.assertTrue(wait_until(lambda: worker._cancels.get(plan.plan_id) is not None))
        worker.cancel(plan.plan_id)
        worker.join(plan.plan_id, timeout=5)
        self.assertEqual(self.registry.get(plan.plan_id).status, PlanStatus.PROPOSING)
        self.assertEqual(self.events, [])

    def test_shutdown_cancels_every_running_proposal(self) -> None:
        gate = threading.Event()
        worker = self._worker(FakeProposer(GOOD, gate=gate))
        plan = self._plan()
        worker.start(plan)
        self.assertTrue(wait_until(lambda: plan.plan_id in worker._cancels))
        worker.shutdown()
        worker.join(plan.plan_id, timeout=5)
        self.assertEqual(self.registry.get(plan.plan_id).status, PlanStatus.PROPOSING)

    def test_a_cancel_mid_reply_is_dropped_even_with_a_reply(self) -> None:
        worker = self._worker(FakeProposer(GOOD))
        plan = self._plan()

        def propose_then_cancel(prompt, *, source_task_id, cancel_event, model=''):
            cancel_event.set()
            return GOOD

        worker._proposer = mock.Mock(propose=propose_then_cancel)
        self.assertEqual(self._run(worker, plan).status, PlanStatus.PROPOSING)

    def test_starting_again_cancels_the_earlier_run(self) -> None:
        gate = threading.Event()
        proposer = FakeProposer(GOOD, gate=gate)
        worker = self._worker(proposer)
        plan = self._plan()
        worker.start(plan)
        self.assertTrue(wait_until(lambda: len(proposer.prompts) == 1))
        first = worker._cancels[plan.plan_id]
        worker.start(plan)
        self.assertTrue(first.is_set())
        gate.set()
        worker.join(plan.plan_id, timeout=5)
        self.assertTrue(wait_until(lambda: self.registry.get(plan.plan_id).status == PlanStatus.PROPOSED))

    def test_a_stale_result_is_dropped(self) -> None:
        worker = self._worker(FakeProposer(GOOD))
        plan = self._plan()
        # The plan moved on (revised) while this proposal ran.
        self.registry.mutate(plan.plan_id, lambda live: setattr(live, 'revision', 5))
        self.assertEqual(self._run(worker, plan).steps, [])
        self.registry.mutate(plan.plan_id, lambda live: setattr(live, 'status', PlanStatus.CANCELLED))
        self.assertEqual(self._run(worker, self.registry.get(plan.plan_id)).status, PlanStatus.CANCELLED)

    def test_a_plan_discarded_meanwhile_is_ignored(self) -> None:
        worker = self._worker(FakeProposer(GOOD))
        plan = self._plan()
        self.registry.remove(plan.plan_id)
        worker.start(plan)
        worker.join(plan.plan_id, timeout=5)
        self.assertIsNone(self.registry.find(plan.plan_id))

    def test_a_registry_failure_other_than_not_found_surfaces(self) -> None:
        from plan_queue_core_lib.plan_queue_core_lib.errors import PlanQueueError
        worker = self._worker(FakeProposer(GOOD))
        plan = self._plan()
        with mock.patch.object(self.registry, 'mutate', side_effect=PlanQueueError('busy')):
            with self.assertRaises(PlanQueueError):
                worker._record(plan, None, 'x')

    def test_an_empty_reply_counts_as_unusable(self) -> None:
        plan = self._run(self._worker(FakeProposer()), self._plan())
        self.assertEqual(plan.status, PlanStatus.PROPOSE_FAILED)

    def test_join_without_a_run_returns(self) -> None:
        self._worker(FakeProposer()).join('f' * 32, timeout=0.1)

    def test_no_observer_is_fine(self) -> None:
        worker = ProposalWorker(self.registry, FakeProposer(GOOD), FakeWording())
        self.assertEqual(self._run(worker, self._plan()).status, PlanStatus.PROPOSED)


if __name__ == '__main__':
    unittest.main()
