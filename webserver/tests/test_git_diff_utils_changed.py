"""Tests for ``changed_paths`` — the helper that powers the green
"modified on this branch" colouring + ``M`` badge in the Files tree.

It unions two git calls (in this order):
  1. ``git diff --name-only <base_ref>``  — tracked committed/uncommitted
  2. ``git ls-files --others --exclude-standard`` — untracked, non-ignored

We stub ``run_git`` with a side-effect sequence rather than spinning
up a real repo with a base ref per test (slow / brittle on CI).
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from kato_webserver import git_diff_utils


class ChangedPathsTests(unittest.TestCase):
    def test_empty_args_short_circuit(self) -> None:
        self.assertEqual(git_diff_utils.changed_paths('', 'origin/main'), [])
        self.assertEqual(git_diff_utils.changed_paths('/repo', ''), [])

    def test_unions_tracked_and_untracked_sorted_deduped(self) -> None:
        tracked = 'src/app.py\nREADME.md\nsrc/app.py\n'   # dup on purpose
        untracked = 'src/new_file.py\nREADME.md\n'        # README also tracked
        with patch.object(
            git_diff_utils, 'run_git', side_effect=[tracked, untracked],
        ):
            self.assertEqual(
                git_diff_utils.changed_paths('/repo', 'origin/main'),
                ['README.md', 'src/app.py', 'src/new_file.py'],
            )

    def test_only_tracked_when_no_untracked(self) -> None:
        with patch.object(
            git_diff_utils, 'run_git', side_effect=['a.py\nb.py\n', ''],
        ):
            self.assertEqual(
                git_diff_utils.changed_paths('/repo', 'origin/main'),
                ['a.py', 'b.py'],
            )

    def test_only_untracked_when_no_tracked_diff(self) -> None:
        with patch.object(
            git_diff_utils, 'run_git', side_effect=['', 'fresh.py\n'],
        ):
            self.assertEqual(
                git_diff_utils.changed_paths('/repo', 'origin/main'),
                ['fresh.py'],
            )

    def test_run_git_failure_degrades_to_empty(self) -> None:
        # run_git → None on both calls (git missing / not a repo /
        # bad base ref). Must degrade quietly, never raise.
        with patch.object(
            git_diff_utils, 'run_git', side_effect=[None, None],
        ):
            self.assertEqual(
                git_diff_utils.changed_paths('/repo', 'origin/bogus'), [],
            )

    def test_strips_whitespace_and_skips_blank_lines(self) -> None:
        with patch.object(
            git_diff_utils, 'run_git',
            side_effect=['  src/x.py  \n\n', '\n  y.py \n'],
        ):
            self.assertEqual(
                git_diff_utils.changed_paths('/repo', 'origin/main'),
                ['src/x.py', 'y.py'],
            )


if __name__ == '__main__':
    unittest.main()
