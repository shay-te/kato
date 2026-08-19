"""A failed git command must explain itself without flooding the log.

A rebase conflict printed 74 lines of ``Rebasing (n/m)`` progress noise
into the caller's log for every attempt, burying the one line that said
what actually went wrong.
"""

from __future__ import annotations

import unittest

from git_core_lib.git_core_lib.client.git_client import GitClientMixin


class GitOutputCondensingTests(unittest.TestCase):
    def test_rebase_progress_is_dropped_but_the_error_survives(self) -> None:
        noisy = '\n'.join(
            [f'Rebasing ({i}/261)' for i in range(1, 71)]
            + ['error: could not apply 946752e0b... Implement UNA-2800',
               'hint: Resolve all conflicts manually, mark them as resolved with',
               'Could not apply 946752e0b... # Implement UNA-2800'],
        )
        out = GitClientMixin._condense_git_output(noisy)
        self.assertNotIn('Rebasing (', out)
        self.assertIn('could not apply', out)
        self.assertIn('hint:', out)

    def test_long_output_is_capped_and_says_so(self) -> None:
        out = GitClientMixin._condense_git_output(
            '\n'.join(f'error: line {i}' for i in range(100)),
        )
        self.assertLessEqual(len(out.splitlines()), GitClientMixin._MAX_DETAIL_LINES + 1)
        self.assertIn('omitted', out)

    def test_a_short_real_error_is_untouched(self) -> None:
        self.assertEqual(
            GitClientMixin._condense_git_output('fatal: not a git repository'),
            'fatal: not a git repository',
        )

    def test_progress_only_output_still_yields_something(self) -> None:
        # Never return an empty reason — "it failed and here is nothing"
        # is the least useful message possible.
        self.assertTrue(GitClientMixin._condense_git_output('Rebasing (1/2)\nRebasing (2/2)'))


if __name__ == '__main__':
    unittest.main()
