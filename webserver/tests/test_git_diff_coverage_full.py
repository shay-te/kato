"""Coverage filler for ``git_diff_utils._diff_base`` early-return guard.

Targets line 259: when ``cwd`` or ``base_ref`` is empty/falsy the
merge-base lookup is skipped and ``base_ref`` is returned verbatim. New
file so it never collides with the existing webserver suites.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from kato_webserver import git_diff_utils


class DiffBaseGuardTests(unittest.TestCase):
    def test_returns_base_ref_when_cwd_is_empty(self) -> None:
        # Empty cwd -> guard on line 258 is True -> line 259 returns the
        # passed base_ref untouched, and run_git is never invoked.
        with patch.object(git_diff_utils, 'run_git') as run_git_mock:
            result = git_diff_utils._diff_base('', 'origin/main')
        self.assertEqual(result, 'origin/main')
        run_git_mock.assert_not_called()

    def test_returns_base_ref_when_base_ref_is_empty(self) -> None:
        with patch.object(git_diff_utils, 'run_git') as run_git_mock:
            result = git_diff_utils._diff_base('/repo', '')
        self.assertEqual(result, '')
        run_git_mock.assert_not_called()

    def test_uses_merge_base_when_both_present(self) -> None:
        # Sanity that the non-guarded path still resolves the merge-base
        # (so the guard isn't masking the real branch).
        with patch.object(
            git_diff_utils, 'run_git', return_value='abc123\n'
        ) as run_git_mock:
            result = git_diff_utils._diff_base('/repo', 'origin/main')
        self.assertEqual(result, 'abc123')
        run_git_mock.assert_called_once_with(
            '/repo', ['merge-base', 'origin/main', 'HEAD'], timeout=10
        )

    def test_falls_back_to_base_ref_when_no_merge_base(self) -> None:
        with patch.object(git_diff_utils, 'run_git', return_value=''):
            result = git_diff_utils._diff_base('/repo', 'origin/main')
        self.assertEqual(result, 'origin/main')


if __name__ == '__main__':
    unittest.main()
