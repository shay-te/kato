"""Telling a failure the task caused from one that was already there.

``hold_to_the_task`` is the one check the loop can make itself on the chat's
"already failing" list: a test whose own test file the task added or changed is
the task's to fix. A test that only imports changed code is not moved: the
chat's stash check is what decides that. Run on real diff text, through
``changed_paths``.
"""
from __future__ import annotations

import unittest

from review_loop_core_lib.review_loop_core_lib.data.state import TestReport
from review_loop_core_lib.review_loop_core_lib.older_failures import (
    CHANGED_BY_TASK_NOTE,
    changed_paths,
    hold_to_the_task,
    is_test_file,
)
from review_loop_core_lib.review_loop_core_lib.ports import RepoDiff


def diff(*paths: str) -> str:
    return ''.join(f'diff --git a/{path} b/{path}\n+x\n' for path in paths)


class ChangedPathsTests(unittest.TestCase):
    def test_every_file_in_every_repository(self) -> None:
        diffs = [RepoDiff('client', diff('src/a.js', 'src/__tests__/a.test.js')),
                 RepoDiff('backend', diff('tests/test_b.py')), RepoDiff('empty', '')]
        self.assertEqual(changed_paths(diffs), {'src/a.js', 'src/__tests__/a.test.js', 'tests/test_b.py'})


class IsTestFileTests(unittest.TestCase):
    def test_test_files_and_test_code(self) -> None:
        for path in ('tests/test_b.py', 'test_b.py', 'pkg/tests.py', 'conftest.py', 'a_test.go',
                     'src/a.test.js', 'src/a.spec.ts', 'spec/models/user_spec.rb', 'src/__tests__/helpers.js',
                     'src/test/java/app/UserTest.java', 'UserTests.cs', 'src/setupTests.js',
                     'src/__snapshots__/a.js.snap', 'test/fixtures/data.json', 'src\\__tests__\\a.js'):
            with self.subTest(path=path):
                self.assertTrue(is_test_file(path))

    def test_code_the_tests_import_is_not(self) -> None:
        for path in ('src/store/system/systemStore.js', 'src/constants.js', 'latest.py', 'contest.py',
                     'manifest.json', 'testing.py', 'src/attest_x.py', 'Contest.java', 'index.js'):
            with self.subTest(path=path):
                self.assertFalse(is_test_file(path))


class HoldToTheTaskTests(unittest.TestCase):
    def report(self, *older: str, failures=()) -> TestReport:
        return TestReport(passed=True, summary='ok', failures=list(failures), already_failing=list(older))

    def test_a_test_whose_file_the_task_changed_goes_back_to_the_failures(self) -> None:
        held = hold_to_the_task(self.report('src/__tests__/a.test.js › adds (same on master)',
                                            'src/__tests__/b.test.js › other (same on master)',
                                            failures=['c: boom']),
                                {'src/__tests__/a.test.js'})
        self.assertIs(held.passed, False)
        self.assertEqual(held.failures, ['c: boom', 'src/__tests__/a.test.js › adds (same on master)' + CHANGED_BY_TASK_NOTE])
        self.assertEqual(held.already_failing, ['src/__tests__/b.test.js › other (same on master)'])

    # UNA-3454: the chat proved four failures older (the same with the task's
    # changes stashed) and kept them as failures anyway, "each imports a file
    # this task changed": the loop ended STUCK again. Importing changed code
    # does not make an older failure the task's.
    def test_a_test_that_only_imports_changed_code_stays_apart(self) -> None:
        older = ("src/__tests__/publicProfileFieldOrder.test.js › lists every field: 'name' missing (same failure "
                 'with changes stashed; imports src/components/User/fieldOrderHelpers.js, src/constants.js)')
        report = self.report(older)
        held = hold_to_the_task(report, {'src/components/User/fieldOrderHelpers.js', 'src/constants.js',
                                         'src/__tests__/userCreationSource.test.js'})
        self.assertIs(held, report)

    def test_only_a_whole_path_counts(self) -> None:
        older = ('src/app/a.test.js › renders (same stashed)', 'tests/test_ab.py (same stashed)',
                 'pkg/tests/test_c.py (same stashed)')
        report = self.report(*older)
        held = hold_to_the_task(report, {'a.test.js', 'test_a', 'test_c.py', ''})
        self.assertIs(held, report)  # nothing named a changed test file: the report as it was
        self.assertEqual(held.already_failing, list(older))

    def test_nothing_changed_or_nothing_claimed_leaves_the_report_alone(self) -> None:
        report = self.report('a.test.js (same stashed)')
        self.assertIs(hold_to_the_task(report, ()), report)
        empty = self.report()
        self.assertIs(hold_to_the_task(empty, {'a.test.js'}), empty)

    def test_a_repository_name_quotes_or_backslashes_around_the_path_still_match(self) -> None:
        for item in ('backend tests/test_b.py: fails', "'tests/test_b.py' fails", '(tests/test_b.py) fails',
                     'backend/tests/test_b.py: fails', 'backend\\tests\\test_b.py: fails'):
            with self.subTest(item=item):
                held = hold_to_the_task(self.report(item), {'tests/test_b.py'})
                self.assertEqual(held.already_failing, [])
                self.assertEqual(held.failures, [item + CHANGED_BY_TASK_NOTE])


if __name__ == '__main__':
    unittest.main()
