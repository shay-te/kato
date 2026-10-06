"""Value types: fingerprints that survive edits, a lossless JSON round trip."""
from __future__ import annotations

import unittest

from review_loop_core_lib.review_loop_core_lib.data.state import (
    TERMINAL_STATUSES,
    FindingSeverity,
    ReviewFinding,
    ReviewLoopPhase,
    ReviewLoopState,
    ReviewLoopStatus,
    ReviewRound,
)


def _finding(**overrides) -> ReviewFinding:
    values = dict(
        severity=FindingSeverity.MAJOR, title='Null deref', file='src/app.py',
        repository='api', line=10, symbol='login', category='correctness', detail='guard it',
    )
    values.update(overrides)
    return ReviewFinding(**values)


class FindingTests(unittest.TestCase):

    def test_the_fingerprint_ignores_line_moves_and_wording(self) -> None:
        moved = _finding(line=99, title='Possible None', detail='other words')
        self.assertEqual(_finding().fingerprint, moved.fingerprint)

    def test_the_fingerprint_ignores_case_and_padding(self) -> None:
        shouty = _finding(file=' SRC/App.py ', symbol='LOGIN', repository='API')
        self.assertEqual(_finding().fingerprint, shouty.fingerprint)

    def test_a_different_place_or_kind_is_a_different_issue(self) -> None:
        base = _finding().fingerprint
        for change in ({'file': 'src/other.py'}, {'symbol': 'logout'},
                       {'category': 'security'}, {'repository': 'web'}):
            with self.subTest(change=change):
                self.assertNotEqual(base, _finding(**change).fingerprint)

    def test_only_blocker_and_major_block(self) -> None:
        blocking = {severity: _finding(severity=severity).is_blocking for severity in FindingSeverity}
        self.assertEqual(blocking, {
            FindingSeverity.BLOCKER: True, FindingSeverity.MAJOR: True,
            FindingSeverity.MINOR: False, FindingSeverity.NIT: False,
        })

    def test_round_trip(self) -> None:
        self.assertEqual(ReviewFinding.from_dict(_finding().to_dict()), _finding())

    def test_missing_optional_fields_default(self) -> None:
        sparse = ReviewFinding.from_dict({'severity': 'NIT'})
        self.assertEqual((sparse.title, sparse.line, sparse.category), ('', 0, 'other'))


class RoundTests(unittest.TestCase):

    def test_counts_and_blocking(self) -> None:
        review_round = ReviewRound(number=1, started_at=1.0, findings=[
            _finding(), _finding(severity=FindingSeverity.NIT, symbol='x'),
            _finding(severity=FindingSeverity.BLOCKER, symbol='y'),
        ])
        self.assertEqual(review_round.counts,
                         {'BLOCKER': 1, 'MAJOR': 1, 'MINOR': 0, 'NIT': 1, 'SETTLED': 0})
        self.assertEqual(len(review_round.blocking), 2)
        self.assertEqual(len(review_round.blocking_fingerprints), 2)

    def test_round_trip(self) -> None:
        original = ReviewRound(
            number=2, started_at=1.0, reviewed_at=2.0, sent_at=3.0, fixed_at=4.0,
            findings=[_finding()], diff_repos=2, diff_files=5, diff_omitted=1, outcome='sent',
        )
        self.assertEqual(ReviewRound.from_dict(original.to_dict()), original)


class LoopStateTests(unittest.TestCase):

    def test_a_new_loop_is_running_and_waiting(self) -> None:
        state = ReviewLoopState.new('T-1', max_rounds=5, now=10.0)
        self.assertEqual(state.status, ReviewLoopStatus.RUNNING)
        self.assertEqual(state.phase, ReviewLoopPhase.WAITING_TO_REVIEW)
        self.assertEqual(state.phase_started_at, 10.0)
        self.assertEqual(len(state.loop_id), 32)
        self.assertFalse(state.is_terminal)
        self.assertEqual(state.round, 0)
        self.assertIsNone(state.current_round)
        self.assertIsNone(state.last_reviewed_round)

    def test_every_status_but_running_is_final(self) -> None:
        self.assertEqual(TERMINAL_STATUSES, set(ReviewLoopStatus) - {ReviewLoopStatus.RUNNING})

    def test_the_summary_reports_the_last_reviewed_rounds_counts(self) -> None:
        state = ReviewLoopState.new('T-1', max_rounds=5, now=1.0)
        state.rounds = [
            ReviewRound(number=1, started_at=1.0, reviewed_at=2.0, findings=[_finding()]),
            ReviewRound(number=2, started_at=3.0),  # under review, no counts yet
        ]
        summary = state.summary()
        self.assertEqual(summary['round'], 2)
        self.assertEqual(summary['counts']['MAJOR'], 1)
        self.assertEqual(summary['status'], 'running')
        self.assertEqual(summary['phase'], 'waiting_to_review')
        self.assertIsNone(ReviewLoopState.new('T-2', max_rounds=5, now=1.0).summary()['counts'])

    def test_round_trip_and_copy_are_independent(self) -> None:
        state = ReviewLoopState.new('T-1', max_rounds=3, now=1.0)
        state.rounds.append(ReviewRound(number=1, started_at=1.0, findings=[_finding()]))
        state.status = ReviewLoopStatus.STUCK
        state.reason = 'no progress'
        copy = state.copy()
        self.assertEqual(copy, state)
        copy.rounds.clear()
        self.assertEqual(len(state.rounds), 1)
        self.assertEqual(ReviewLoopState.from_dict(state.to_dict()), state)

    def test_a_minimal_saved_state_reads_back_as_finished(self) -> None:
        state = ReviewLoopState.from_dict({
            'task_id': 'T-1', 'loop_id': 'a' * 32, 'max_rounds': 5, 'started_at': 1,
        })
        self.assertEqual(state.status, ReviewLoopStatus.RUNNING)
        self.assertEqual(state.phase, ReviewLoopPhase.DONE)


if __name__ == '__main__':
    unittest.main()
