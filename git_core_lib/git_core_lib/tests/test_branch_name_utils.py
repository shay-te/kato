"""Branch names a person typed, held against git itself."""

from __future__ import annotations

import subprocess
import unittest

from git_core_lib.git_core_lib.helpers.branch_name_utils import (
    MAX_BRANCH_NAME_CHARS,
    branch_name_problem,
)

GOOD_NAMES = (
    'feature/login',
    'feature/UNA-123_fix.v2',
    'fix-retry',
    'release/1.2.3',
    'a',
    '1-hotfix',
    'origins/main',
    'lock',
    'x' * MAX_BRANCH_NAME_CHARS,
)
# Each of these git itself refuses.
REFUSED_BY_GIT = (
    '-rf',
    '.hidden',
    'a/.hidden',
    'a..b',
    'a//b',
    'a/',
    '/a',
    'a.',
    'a.lock',
    'a.lock/b',
    'a b',
    'a~1',
    'a^',
    'a:b',
    'a?',
    'a*',
    'a[b',
    'a\\b',
    'a@{b',
    'HEAD',
    'tab\tname',
)
# Git would take these; they are refused for what they do later.
REFUSED_BY_US = (
    '@',
    'origin/feature',
    'ORIGIN/feature',
    'refs/heads/feature',
    'head',
    '_private',
    'שלום',
    'a#b',
    'x' * (MAX_BRANCH_NAME_CHARS + 1),
)


def git_accepts(name: str) -> bool:
    result = subprocess.run(
        ['git', 'check-ref-format', '--branch', name], capture_output=True, check=False,
    )
    return result.returncode == 0


class BranchNameProblemTests(unittest.TestCase):
    def test_good_names_have_no_problem_and_git_agrees(self) -> None:
        for name in GOOD_NAMES:
            with self.subTest(name=name):
                self.assertEqual(branch_name_problem(name), '')
                self.assertTrue(git_accepts(name))

    def test_every_name_git_refuses_is_refused(self) -> None:
        for name in REFUSED_BY_GIT:
            with self.subTest(name=name):
                self.assertFalse(git_accepts(name))
                self.assertNotEqual(branch_name_problem(name), '')

    def test_names_git_would_take_can_still_be_refused(self) -> None:
        for name in REFUSED_BY_US:
            with self.subTest(name=name):
                self.assertNotEqual(branch_name_problem(name), '')

    def test_an_empty_name_is_a_problem(self) -> None:
        self.assertEqual(branch_name_problem(''), 'the name is empty')

    def test_each_problem_says_what_to_change(self) -> None:
        for name, words in (
            ('x' * (MAX_BRANCH_NAME_CHARS + 1), 'longer than'),
            ('a b', 'letters, digits'),
            ('-rf', 'start with a letter'),
            ('HEAD', 'git keeps for itself'),
            ('origin/feature', 'leave out origin/'),
            ('a..b', '..'),
            ('a//b', 'between slashes'),
            ('a/', 'between slashes'),
            ('a/.b', 'start with a dot'),
            ('a.lock', '.lock'),
            ('a.', 'end with a dot'),
        ):
            with self.subTest(name=name):
                self.assertIn(words, branch_name_problem(name))


if __name__ == '__main__':
    unittest.main()
