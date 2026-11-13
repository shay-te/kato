"""Telling a failure the task caused from one that was already there.

``hold_to_the_task`` is the one check the loop can make itself on the chat's
"already failing" list: a test that names a file the task changed is the
task's to fix. Run on real diff text, through ``changed_paths``.
"""
from __future__ import annotations

import unittest

from review_loop_core_lib.review_loop_core_lib.data.state import TestReport
from review_loop_core_lib.review_loop_core_lib.older_failures import (
    CHANGED_BY_TASK_NOTE,
    changed_paths,
    hold_to_the_task,
)
from review_loop_core_lib.review_loop_core_lib.ports import RepoDiff


def diff(*paths: str) -> str:
    return ''.join(f'diff --git a/{path} b/{path}\n+x\n' for path in paths)


class ChangedPathsTests(unittest.TestCase):
    def test_every_file_in_every_repository(self) -> None:
        diffs = [RepoDiff('client', diff('src/a.js', 'src/__tests__/a.test.js')),
                 RepoDiff('backend', diff('tests/test_b.py')), RepoDiff('empty', '')]
        self.assertEqual(changed_paths(diffs), {'src/a.js', 'src/__tests__/a.test.js', 'tests/test_b.py'})


class HoldToTheTaskTests(unittest.TestCase):
    def report(self, *older: str, failures=()) -> TestReport:
        return TestReport(passed=True, summary='ok', failures=list(failures), already_failing=list(older))

    def test_a_test_naming_a_changed_file_goes_back_to_the_failures(self) -> None:
        held = hold_to_the_task(self.report('src/__tests__/a.test.js › adds (same on master)',
                                            'src/__tests__/b.test.js › other (same on master)',
                                            failures=['c: boom']),
                                {'src/__tests__/a.test.js'})
        self.assertIs(held.passed, False)
        self.assertEqual(held.failures, ['c: boom', 'src/__tests__/a.test.js › adds (same on master)' + CHANGED_BY_TASK_NOTE])
        self.assertEqual(held.already_failing, ['src/__tests__/b.test.js › other (same on master)'])

    def test_a_source_file_the_failure_names_counts_too(self) -> None:
        held = hold_to_the_task(self.report('b.test.js: TypeError in src/utils/x.js'), {'src/utils/x.js'})
        self.assertEqual(held.already_failing, [])

    def test_only_a_whole_path_counts(self) -> None:
        older = ('src/app/index.js › renders (same stashed)', 'tests/test_ab.py (same stashed)')
        report = self.report(*older)
        held = hold_to_the_task(report, {'index.js', 'test_a', 'b.py', ''})
        self.assertIs(held, report)  # nothing named a changed file: the report as it was
        self.assertEqual(held.already_failing, list(older))

    def test_nothing_changed_or_nothing_claimed_leaves_the_report_alone(self) -> None:
        report = self.report('a.test.js (same stashed)')
        self.assertIs(hold_to_the_task(report, ()), report)
        empty = self.report()
        self.assertIs(hold_to_the_task(empty, {'a.test.js'}), empty)

    def test_a_repository_prefix_or_quotes_around_the_path_still_match(self) -> None:
        for item in ('backend tests/test_b.py: fails', "'tests/test_b.py' fails", '(tests/test_b.py) fails'):
            with self.subTest(item=item):
                self.assertEqual(hold_to_the_task(self.report(item), {'tests/test_b.py'}).already_failing, [])


if __name__ == '__main__':
    unittest.main()
