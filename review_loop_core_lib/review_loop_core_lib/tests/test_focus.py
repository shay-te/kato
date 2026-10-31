"""A loop stays on its task, however many rounds it runs.

Every round used to read the whole change again and hold it to everything that
could be better. A fresh reader always finds something; each fix gave the next
one more code to find it in. Measured on saved loops: at least 25 of the 50
blockers after round 1 were in files no fix had touched, and UNA-3348 grew from
7 files to 17. So a later round re-checks what moved, a finding the task has
nothing to do with never blocks, and a sweep does not reopen what was ruled out.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from review_loop_core_lib.review_loop_core_lib.data.state import (
    ReviewLoopState,
    ReviewLoopStatus,
)
from review_loop_core_lib.review_loop_core_lib.findings_prompt import (
    RESPONSE_CLOSE,
    RESPONSE_OPEN,
)
from review_loop_core_lib.review_loop_core_lib.ports import RepoDiff
from review_loop_core_lib.review_loop_core_lib.service import ReviewLoopService
from review_loop_core_lib.review_loop_core_lib.store import ArtifactKind, ReviewLoopStore
from review_loop_core_lib.review_loop_core_lib.tests.fakes import (
    FAST,
    WORDING,
    FakeChat,
    FakeReviewer,
    FakeTree,
    finding,
    reply,
    wait_until,
)

RECHECK_HEADING = '## This round is a re-check'
WHOLE_HEADING = '## The changes ('


def answers(*decisions: dict) -> str:
    return f'Done.\n{RESPONSE_OPEN}\n{json.dumps({"decisions": list(decisions)})}\n{RESPONSE_CLOSE}'


class StoreFromBeforeRechecks(ReviewLoopStore):
    """What a loop saved before re-checks existed reads back as: no digests."""

    def read_artifact(self, task_id, loop_id, round_number, kind):
        if kind is ArtifactKind.FILES:
            return None
        return super().read_artifact(task_id, loop_id, round_number, kind)


class EditedDuringASecondReview(FakeReviewer):
    """The operator keeps chatting while round 2 reads: the tree moves under it."""

    def __init__(self, tree: FakeTree, *replies: str) -> None:
        super().__init__(*replies)
        self.tree = tree

    def review(self, prompt, *, task_id, cancel_event, model=''):
        if len(self.prompts) == 1:
            self.tree.files['lib.py'] = 'print(20)\n'
        return super().review(prompt, task_id=task_id, cancel_event=cancel_event, model=model)


class _Focus(unittest.TestCase):

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.store = ReviewLoopStore(self.root)
        self.tree = FakeTree({'app.py': 'print(1)\n', 'lib.py': 'print(2)\n'})

    def run_loop(self, chat, reviewer, *, diff_source=None, **options) -> ReviewLoopState:
        service = ReviewLoopService(
            store=self.store, chat=chat, reviewer=reviewer, wording=WORDING, options=FAST,
        )
        self.addCleanup(service.shutdown, 'test over')
        service.start('T-1', diff_source=diff_source or self.tree.diff_source, **options)
        self.assertTrue(wait_until(lambda: not service.is_running('T-1')))
        return service.state('T-1')

    def fix_app(self, _prompt: str = '') -> None:
        self.tree.files['app.py'] = 'print(10)\n'

    def artifact(self, state, number: int, kind: ArtifactKind) -> str | None:
        return self.store.read_artifact('T-1', state.loop_id, number, kind)


class ReCheckTests(_Focus):

    def test_the_first_review_reads_the_whole_change(self) -> None:
        reviewer = FakeReviewer(reply())
        state = self.run_loop(FakeChat(), reviewer)
        self.assertIn(WHOLE_HEADING, reviewer.prompts[0])
        self.assertNotIn(RECHECK_HEADING, reviewer.prompts[0])
        self.assertEqual(state.rounds[0].rechecked_files, 0)

    def test_a_later_round_reads_only_what_moved(self) -> None:
        reviewer = FakeReviewer(reply(finding('MAJOR')), reply())
        state = self.run_loop(FakeChat(fixer=self.fix_app), reviewer)

        self.assertEqual(state.status, ReviewLoopStatus.CLEAN, state.reason)
        self.assertEqual([r.rechecked_files for r in state.rounds], [0, 1])
        second = reviewer.prompts[1]
        self.assertIn(RECHECK_HEADING, second)
        self.assertNotIn(WHOLE_HEADING, second)
        self.assertIn('-+print(1)', second)
        self.assertIn('++print(10)', second)
        self.assertNotIn('print(2)', second)  # lib.py did not move

    def test_each_round_keeps_the_whole_change_to_compare_the_next_with(self) -> None:
        state = self.run_loop(
            FakeChat(fixer=self.fix_app), FakeReviewer(reply(finding('MAJOR')), reply()),
        )
        self.assertIn('print(2)', self.artifact(state, 2, ArtifactKind.DIFF))
        self.assertNotIn('print(2)', self.artifact(state, 2, ArtifactKind.CHANGES))

    def test_a_re_check_starts_from_the_last_review_not_the_first(self) -> None:
        fixes = iter(({'app.py': 'print(10)\n'}, {'lib.py': 'print(20)\n'}))
        reviewer = FakeReviewer(reply(finding('MAJOR')), reply(finding('MAJOR', file='lib.py')), reply())
        state = self.run_loop(
            FakeChat(fixer=lambda prompt: self.tree.files.update(next(fixes))), reviewer,
        )
        self.assertEqual(state.status, ReviewLoopStatus.CLEAN, state.reason)
        self.assertIn('++print(20)', reviewer.prompts[2])
        self.assertNotIn('print(10)', reviewer.prompts[2])

    def test_a_fix_that_changed_nothing_reads_the_whole_change(self) -> None:
        reviewer = FakeReviewer(reply(finding('MAJOR')), reply())
        state = self.run_loop(FakeChat(), reviewer)
        self.assertIn(WHOLE_HEADING, reviewer.prompts[1])
        self.assertEqual(state.rounds[1].rechecked_files, 0)

    def test_a_clean_re_check_says_it_covered_only_what_moved(self) -> None:
        state = self.run_loop(
            FakeChat(fixer=self.fix_app), FakeReviewer(reply(finding('MAJOR')), reply()),
        )
        self.assertEqual(
            state.reason, 'round 2 found no blocking issues in what changed since the last review',
        )

    def test_a_loop_saved_before_re_checks_reads_the_whole_change(self) -> None:
        self.store = StoreFromBeforeRechecks(self.root)
        reviewer = FakeReviewer(reply(finding('MAJOR')), reply())
        self.run_loop(FakeChat(fixer=self.fix_app), reviewer)
        self.assertIn(WHOLE_HEADING, reviewer.prompts[1])

    def test_a_repository_it_cannot_read_means_the_whole_change(self) -> None:
        # Its files would look "taken out", and the reviewer would never hear why.
        def diffs(task_id: str) -> list[RepoDiff]:
            broken = RepoDiff(repo_id='web', diff='', error='no such branch')
            read = self.tree.diff_source(task_id)
            return read + [broken] if self.tree.files['app.py'] == 'print(10)\n' else read

        reviewer = FakeReviewer(reply(finding('MAJOR')), reply())
        self.run_loop(FakeChat(fixer=self.fix_app), reviewer, diff_source=diffs)
        self.assertIn(WHOLE_HEADING, reviewer.prompts[1])
        self.assertIn('no such branch', reviewer.prompts[1])


class SweepTests(_Focus):

    def sweep(self, *decisions: dict) -> tuple[ReviewLoopState, str]:
        reviewer = FakeReviewer(
            reply(finding('MAJOR', title='fixed one'),
                  finding('MAJOR', file='lib.py', title='not needed')),
            reply(),
        )
        chat = FakeChat(fixer=self.fix_app, reply=answers(*decisions))
        state = self.run_loop(chat, reviewer, confirm_clean=True)
        self.assertEqual(state.status, ReviewLoopStatus.CLEAN, state.reason)
        self.assertEqual([r.sweep for r in state.rounds], [False, False, True])
        return state, reviewer.prompts[2]

    def test_a_sweep_reads_the_whole_change(self) -> None:
        state, prompt = self.sweep({'id': 'R1-1', 'decision': 'fixed', 'test': 't.py::t'})
        self.assertIn(WHOLE_HEADING, prompt)
        self.assertIn('print(2)', prompt)
        self.assertEqual(state.rounds[2].rechecked_files, 0)

    def test_a_sweep_reads_the_whole_change_even_when_something_moved(self) -> None:
        # A sweep is what puts every line in front of a reader at its final
        # version. As a re-check of what moved, it would not.
        reviewer = EditedDuringASecondReview(self.tree, reply(finding('MAJOR')), reply())
        state = self.run_loop(FakeChat(fixer=self.fix_app), reviewer, extra_sweep=True)

        self.assertEqual([r.sweep for r in state.rounds], [False, False, True])
        self.assertEqual(state.rounds[2].rechecked_files, 0)
        self.assertIn(WHOLE_HEADING, reviewer.prompts[2])
        self.assertNotIn(RECHECK_HEADING, reviewer.prompts[2])
        self.assertIn('print(20)', reviewer.prompts[2])

    def test_a_sweep_is_told_what_was_ruled_out_and_not_what_was_fixed(self) -> None:
        # UNA-3348 round 5: 7 raised again by a reader told nothing, 3 caught by
        # fingerprint, and the other 4 went back to the chat in new words.
        _, prompt = self.sweep(
            {'id': 'R1-1', 'decision': 'fixed', 'test': 't.py::t', 'evidence': 'changed it'},
            {'id': 'R1-2', 'decision': 'out_of_scope', 'evidence': 'the ticket is about X'},
        )
        self.assertIn('## Decisions so far', prompt)
        self.assertIn('not needed: out_of_scope', prompt)
        self.assertIn('the ticket is about X', prompt)
        self.assertNotIn('fixed one', prompt)

    def test_a_ruling_without_evidence_is_not_told(self) -> None:
        # It settled nothing, so the sweep is free to raise it.
        _, prompt = self.sweep({'id': 'R1-2', 'decision': 'rejected', 'evidence': ''})
        self.assertNotIn('## Decisions so far', prompt)

    def test_a_sweep_still_counts_as_clean_room(self) -> None:
        state, _ = self.sweep({'id': 'R1-2', 'decision': 'out_of_scope', 'evidence': 'proof'})
        self.assertEqual([r.blind for r in state.rounds], [True, False, True])


class OffTaskTests(_Focus):

    def test_an_unrelated_major_is_shown_and_never_sent(self) -> None:
        chat = FakeChat()
        state = self.run_loop(chat, FakeReviewer(reply({**finding('MAJOR'), 'relevance': 'unrelated'})))

        self.assertEqual(state.status, ReviewLoopStatus.CLEAN, state.reason)
        self.assertEqual(chat.delivered, [])
        self.assertEqual(state.reason, 'round 1 found no blocking issues (1 not part of this task)')
        counts = state.rounds[0].counts
        self.assertEqual((counts['MAJOR'], counts['OFF_TASK']), (0, 1))
        self.assertEqual(len(state.rounds[0].findings), 1)  # kept for the operator

    def test_the_reason_names_settled_and_off_task_together(self) -> None:
        reviewer = FakeReviewer(
            reply(finding('MAJOR')),
            reply(finding('MAJOR'), {**finding('MAJOR', file='lib.py'), 'relevance': 'unrelated'}),
        )
        chat = FakeChat(reply=answers({'id': 'R1-1', 'decision': 'rejected', 'evidence': 'proof'}))
        state = self.run_loop(chat, reviewer)
        self.assertEqual(
            state.reason,
            'round 2 found no blocking issues (1 raised again were settled earlier; '
            '1 not part of this task)',
        )

    def test_what_belongs_to_the_task_still_blocks(self) -> None:
        for relevance in ('task', 'regression', ''):
            with self.subTest(relevance=relevance):
                chat = FakeChat(fixer=self.fix_app)
                reviewer = FakeReviewer(
                    reply({**finding('MAJOR'), 'relevance': relevance}), reply(),
                )
                state = self.run_loop(chat, reviewer)
                self.assertEqual(len(chat.delivered), 1)
                self.assertEqual(state.rounds[0].outcome, 'sent')
                self.tree.files['app.py'] = 'print(1)\n'

    def test_a_word_nobody_defined_proves_nothing_about_a_bug(self) -> None:
        chat = FakeChat(fixer=self.fix_app)
        reviewer = FakeReviewer(reply({**finding('BLOCKER'), 'relevance': 'meh'}), reply())
        state = self.run_loop(chat, reviewer)
        self.assertEqual(len(chat.delivered), 1)
        self.assertEqual(state.rounds[0].findings[0].relevance, '')


if __name__ == '__main__':
    unittest.main()
