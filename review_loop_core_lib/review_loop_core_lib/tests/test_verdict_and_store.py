"""Reading verdicts strictly, deciding "stuck", and keeping loops on disk."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from review_loop_core_lib.review_loop_core_lib.data.state import (
    FindingSeverity,
    ReviewLoopState,
    ReviewLoopStatus,
)
from review_loop_core_lib.review_loop_core_lib.reviewer_prompt import (
    VERDICT_CLOSE,
    VERDICT_OPEN,
)
from review_loop_core_lib.review_loop_core_lib.store import (
    ArtifactKind,
    ReviewLoopStore,
)
from review_loop_core_lib.review_loop_core_lib.tests.fakes import finding, reply
from review_loop_core_lib.review_loop_core_lib.verdict import (
    STUCK_AFTER_MISSED_FIXES,
    ReviewVerdictError,
    count_missed_fixes,
    is_stuck,
    mark_repeats,
    parse_review_verdict,
)


def _block(body: str) -> str:
    return f'{VERDICT_OPEN}\n{body}\n{VERDICT_CLOSE}'


class ParseVerdictTests(unittest.TestCase):

    def test_reads_findings_and_counts(self) -> None:
        verdict = parse_review_verdict(reply(finding('blocker'), finding('NIT', symbol='y')))
        self.assertEqual(verdict.findings[0].severity, FindingSeverity.BLOCKER)
        self.assertEqual(verdict.counts, {'BLOCKER': 1, 'MAJOR': 0, 'MINOR': 0, 'NIT': 1, 'SETTLED': 0})
        self.assertFalse(verdict.is_clean)
        self.assertEqual(len(verdict.blocking), 1)

    def test_an_empty_list_is_clean(self) -> None:
        self.assertTrue(parse_review_verdict(reply()).is_clean)

    def test_the_last_block_wins(self) -> None:
        # A reviewer quoting the format first must not be read as its answer.
        text = _block('{"findings": [{"severity": "MAJOR", "title": "x"}]}') + '\n' + reply()
        self.assertTrue(parse_review_verdict(text).is_clean)

    def test_unreadable_replies_raise_never_guess(self) -> None:
        for text in (
            'All good!',
            f'{VERDICT_OPEN} not closed',
            f'{VERDICT_CLOSE} {VERDICT_OPEN}',
            _block('not json'),
            _block('[]'),
            _block('{"findings": "none"}'),
            _block('{"findings": ["a string"]}'),
            _block('{"findings": [{"severity": "CRITICAL", "title": "x"}]}'),
        ):
            with self.subTest(text=text), self.assertRaises(ReviewVerdictError):
                parse_review_verdict(text)

    def test_sloppy_fields_are_normalised(self) -> None:
        verdict = parse_review_verdict(_block(json.dumps({'findings': [{
            'severity': ' major ', 'category': 'Made-Up', 'line': 'twelve', 'title': '  ',
        }, {'severity': 'MINOR', 'line': -4, 'category': 'Security'}]})))
        first, second = verdict.findings
        self.assertEqual(first.severity, FindingSeverity.MAJOR)
        self.assertEqual(first.category, 'other')
        self.assertEqual(first.line, 0)
        self.assertEqual(first.title, '(untitled finding)')
        self.assertEqual(second.line, 0)
        self.assertEqual(second.category, 'security')


class StuckTests(unittest.TestCase):

    def test_a_miss_is_counted_only_for_a_claimed_fix_still_found(self) -> None:
        missed = count_missed_fixes({'a': 1}, claimed_fixed={'a', 'b'}, current={'a', 'c'})
        self.assertEqual(missed, {'a': 2})          # b went away; c is new
        self.assertEqual(count_missed_fixes({}, set(), {'a'}), {})

    def test_stuck_only_when_everything_left_survived_two_fixes(self) -> None:
        self.assertEqual(STUCK_AFTER_MISSED_FIXES, 2)
        self.assertTrue(is_stuck({'a'}, {'a': 2}))
        self.assertTrue(is_stuck({'a', 'b'}, {'a': 3, 'b': 2}))
        self.assertFalse(is_stuck({'a'}, {'a': 1}))             # one miss: once more
        self.assertFalse(is_stuck({'a', 'c'}, {'a': 2}))        # c is new work
        self.assertFalse(is_stuck(set(), {'a': 5}))             # nothing left
        self.assertTrue(is_stuck({'a'}, {'a': 1}, after=1))

    def test_repeats_are_marked_with_their_first_id_and_misses(self) -> None:
        verdict = parse_review_verdict(reply(
            finding('MAJOR', symbol='run'), finding('MAJOR', symbol='load'),
            finding('MINOR', symbol='run', category='style'),
        ), round_number=3)
        run, load, minor = verdict.findings
        missed = {run.fingerprint: 1, minor.fingerprint: 4}
        marked = mark_repeats(verdict.findings, missed, {run.fingerprint: 'R1-1'})
        self.assertEqual([(f.repeat_of, f.missed_fixes) for f in marked],
                         [('R1-1', 1), ('', 0), ('', 0)])   # a non-blocking one is never marked
        unknown_first = mark_repeats([load], {load.fingerprint: 1}, {})
        self.assertEqual(unknown_first[0].repeat_of, load.id)


class StoreTests(unittest.TestCase):

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.store = ReviewLoopStore(self.root, keep_loops=2)

    def _state(self, task_id: str = 'UNA-1', started: float = 1.0) -> ReviewLoopState:
        return ReviewLoopState.new(task_id, max_rounds=5, now=started)

    def test_save_and_read_back_the_newest(self) -> None:
        old, new = self._state(started=1.0), self._state(started=2.0)
        self.assertTrue(self.store.save(old))
        self.assertTrue(self.store.save(new))
        self.assertEqual(self.store.latest('UNA-1').loop_id, new.loop_id)
        self.assertIsNone(self.store.latest('UNA-2'))
        self.assertEqual(self.store.task_ids(), ['UNA-1'])
        self.assertEqual(list(self.store.latest_by_task()), ['UNA-1'])

    def test_artifacts_round_trip_by_kind(self) -> None:
        state = self._state()
        for kind in ArtifactKind:
            self.assertTrue(self.store.write_artifact(state, 1, kind, f'{kind.value} text'))
        for kind in ArtifactKind:
            self.assertEqual(
                self.store.read_artifact('UNA-1', state.loop_id, 1, kind), f'{kind.value} text',
            )
        self.assertIsNone(self.store.read_artifact('UNA-1', state.loop_id, 2, ArtifactKind.DIFF))

    def test_ids_from_outside_can_never_escape_the_root(self) -> None:
        state = self._state()
        for task_id in ('../etc', 'a/b', '', '.hidden', 'x' * 200, 'a..b'):
            with self.subTest(task_id=task_id):
                self.assertFalse(self.store.is_valid_task_id(task_id))
                with self.assertRaises(ValueError):
                    self.store.read_artifact(task_id, state.loop_id, 1, ArtifactKind.DIFF)
        for loop_id in ('../../x', 'ABC', ''):
            with self.subTest(loop_id=loop_id), self.assertRaises(ValueError):
                self.store.read_artifact('UNA-1', loop_id, 1, ArtifactKind.DIFF)
        with self.assertRaises(ValueError):
            self.store.read_artifact('UNA-1', state.loop_id, 0, ArtifactKind.DIFF)

    def test_prune_keeps_the_newest_loops(self) -> None:
        states = [self._state(started=float(n)) for n in range(1, 5)]
        for state in states:
            self.store.save(state)
        self.assertEqual(self.store.prune('UNA-1'), 2)
        kept = {path.name for path in (self.root / 'UNA-1').iterdir()}
        self.assertEqual(kept, {states[2].loop_id, states[3].loop_id})

    def test_a_closed_task_is_never_written_back_into_existence(self) -> None:
        state = self._state()
        self.store.save(state)
        self.store.close('UNA-1')
        self.store.delete_task('UNA-1')
        self.assertFalse(self.store.save(state))
        self.assertFalse(self.store.write_artifact(state, 1, ArtifactKind.DIFF, 'late'))
        self.assertFalse((self.root / 'UNA-1').exists())
        self.store.open('UNA-1')
        self.assertTrue(self.store.save(state))

    def test_torn_and_foreign_entries_are_skipped(self) -> None:
        good = self._state(started=1.0)
        self.store.save(good)
        torn = self.root / 'UNA-1' / ('b' * 32)
        torn.mkdir()
        (torn / 'state.json').write_text('{ torn')
        (self.root / 'UNA-1' / 'notes.txt').write_text('not a loop')
        (self.root / 'UNA-1' / 'not-a-loop-id').mkdir()
        (self.root / 'not a task id').mkdir()
        self.assertEqual(self.store.latest('UNA-1').loop_id, good.loop_id)
        self.assertEqual(self.store.task_ids(), ['UNA-1'])

    def test_a_task_whose_only_loop_is_torn_is_left_out(self) -> None:
        self.store.save(self._state('UNA-1'))
        torn = self.root / 'UNA-2' / ('c' * 32)
        torn.mkdir(parents=True)
        (torn / 'state.json').write_text('{ torn')
        self.assertEqual(list(self.store.latest_by_task()), ['UNA-1'])

    def test_an_unreadable_artifact_is_none(self) -> None:
        state = self._state()
        path = self.root / 'UNA-1' / state.loop_id
        path.mkdir(parents=True)
        (path / 'round-1-review.json').write_text('[1, 2]')
        (path / 'round-1-diff.json').write_text('{ torn')
        self.assertIsNone(self.store.read_artifact('UNA-1', state.loop_id, 1, ArtifactKind.REVIEW))
        self.assertIsNone(self.store.read_artifact('UNA-1', state.loop_id, 1, ArtifactKind.DIFF))

    def test_an_empty_root_has_no_tasks(self) -> None:
        self.assertEqual(ReviewLoopStore(self.root / 'missing').task_ids(), [])

    def test_a_finished_state_reads_back_unchanged(self) -> None:
        state = self._state()
        state.status = ReviewLoopStatus.CLEAN
        self.store.save(state)
        self.assertEqual(self.store.latest('UNA-1'), state)


if __name__ == '__main__':
    unittest.main()
