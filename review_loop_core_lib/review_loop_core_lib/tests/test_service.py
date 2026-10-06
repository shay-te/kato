"""The service's own rules: who may start, one loop per task, what it reports."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from review_loop_core_lib.review_loop_core_lib.data.state import (
    ReviewLoopState,
    ReviewLoopStatus,
    ReviewRound,
)
from review_loop_core_lib.review_loop_core_lib.service import (
    ReviewLoopError,
    ReviewLoopService,
)
from review_loop_core_lib.review_loop_core_lib.store import ReviewLoopStore
from review_loop_core_lib.review_loop_core_lib.tests.fakes import (
    FAST,
    WORDING,
    FakeChat,
    FakeReviewer,
    FakeTree,
    reply,
    wait_until,
)


class ServiceTests(unittest.TestCase):

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.store = ReviewLoopStore(self.root)
        self.tree = FakeTree()

    def service(self, reviewer=None, **kwargs) -> ReviewLoopService:
        service = ReviewLoopService(
            store=self.store, chat=FakeChat(), reviewer=reviewer or FakeReviewer(reply()),
            wording=WORDING, options=FAST, **kwargs,
        )
        self.addCleanup(service.shutdown, 'test over')
        return service

    def test_the_host_can_refuse_with_a_reason(self) -> None:
        service = self.service(can_start=lambda task_id: 'the agent backend cannot review')
        with self.assertRaisesRegex(ReviewLoopError, 'cannot review'):
            service.start('T-1', diff_source=self.tree.diff_source)
        self.assertIsNone(service.state('T-1'))

    def test_a_task_id_that_cannot_name_a_folder_is_refused(self) -> None:
        with self.assertRaisesRegex(ReviewLoopError, 'not a task id'):
            self.service().start('../x', diff_source=self.tree.diff_source)

    def test_one_running_loop_per_task(self) -> None:
        reviewer = FakeReviewer(block_until_cancelled=True)
        service = self.service(reviewer)
        first = service.start('T-1', diff_source=self.tree.diff_source)
        self.assertEqual(first.status, ReviewLoopStatus.RUNNING)
        self.assertTrue(service.is_running('T-1'))
        with self.assertRaisesRegex(ReviewLoopError, 'already running'):
            service.start('T-1', diff_source=self.tree.diff_source)
        service.stop('T-1')
        self.assertTrue(wait_until(lambda: not service.is_running('T-1')))
        # Once it ended, a new one may start — and it is a NEW loop.
        reviewer.block_until_cancelled = False
        reviewer.replies = [reply()]
        second = service.start('T-1', diff_source=self.tree.diff_source)
        self.assertNotEqual(second.loop_id, first.loop_id)

    def test_summaries_cover_running_and_finished_loops(self) -> None:
        finished = ReviewLoopState.new('T-2', max_rounds=5, now=1.0)
        finished.status = ReviewLoopStatus.CLEAN
        finished.rounds.append(ReviewRound(number=1, started_at=1.0, reviewed_at=2.0))
        self.store.save(finished)
        reviewer = FakeReviewer(block_until_cancelled=True)
        service = self.service(reviewer)
        service.start('T-1', diff_source=self.tree.diff_source)
        self.assertTrue(reviewer.started.wait(5))
        summaries = service.summaries()
        self.assertEqual(summaries['T-1']['status'], 'running')
        self.assertEqual(summaries['T-1']['phase'], 'reviewing')
        self.assertEqual(summaries['T-2']['status'], 'clean')
        self.assertEqual(service.state('T-2').loop_id, finished.loop_id)

    def test_artifact_lookups_with_bad_input_are_just_missing(self) -> None:
        service = self.service()
        self.assertIsNone(service.artifact('T-1', 'a' * 32, 1, 'not-a-kind'))
        self.assertIsNone(service.artifact('../x', 'a' * 32, 1, 'diff'))
        self.assertIsNone(service.artifact('T-1', 'a' * 32, 'one', 'diff'))

    def test_forgetting_a_task_without_a_loop_is_harmless(self) -> None:
        service = self.service()
        service.forget('T-3')
        self.assertIsNone(service.state('T-3'))

    def test_shutdown_with_nothing_running_is_harmless(self) -> None:
        self.service().shutdown('bye')

    def test_an_interrupted_loop_closes_the_round_it_was_in(self) -> None:
        orphan = ReviewLoopState.new('T-4', max_rounds=5, now=1.0)
        orphan.rounds = [
            ReviewRound(number=1, started_at=1.0, outcome='sent'),
            ReviewRound(number=2, started_at=2.0),
        ]
        self.store.save(orphan)
        service = self.service()
        self.assertEqual(service.mark_interrupted('restarted'), 1)
        state = service.state('T-4')
        self.assertEqual([r.outcome for r in state.rounds], ['sent', 'stopped'])

    def test_max_rounds_below_one_means_one(self) -> None:
        service = self.service(max_rounds=0)
        state = service.start('T-1', diff_source=self.tree.diff_source)
        self.assertEqual(state.max_rounds, 1)

    def test_the_operator_picks_the_rounds_for_each_loop(self) -> None:
        reviewer = FakeReviewer(block_until_cancelled=True)
        service = self.service(reviewer)
        for task_id, requested, expected in (('T-1', 3, 3), ('T-2', None, 5),
                                             ('T-3', 0, 1), ('T-4', 30, 30), ('T-5', 99, 30)):
            with self.subTest(requested=requested):
                state = service.start(task_id, diff_source=self.tree.diff_source,
                                      max_rounds=requested)
                self.assertEqual(state.max_rounds, expected)

    def test_a_default_above_the_limit_is_held_to_it(self) -> None:
        self.assertEqual(
            self.service(max_rounds=50).start('T-1', diff_source=self.tree.diff_source).max_rounds,
            30,
        )

    def test_a_finished_loop_is_remembered_even_if_its_save_failed(self) -> None:
        service = self.service()
        service.start('T-1', diff_source=self.tree.diff_source)
        self.assertTrue(wait_until(lambda: not service.is_running('T-1')))
        self.assertEqual(service.state('T-1').status, ReviewLoopStatus.CLEAN)


if __name__ == '__main__':
    unittest.main()
