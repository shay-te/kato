"""Files-tree right-click → Discard changes.

An operator-driven ``git restore`` on ONE file. Deliberately not routed
through the agent: it works with no session running, costs no turn, and
cannot be reinterpreted by a model.

Named for the effect, not for a git subcommand: "revert" would read as
``git revert`` (a NEW commit undoing an old one). This runs
``git restore``.

It destroys uncommitted work, and since nothing is committed until
publish there is no commit and no reflog to recover from — so the path
handling is the load-bearing part.
"""

from __future__ import annotations

import unittest
from unittest import mock

from git_core_lib.git_core_lib.client.git_client import GitClientMixin


class SafePathspecTests(unittest.TestCase):
    """A whole-tree discard must be INEXPRESSIBLE, not merely discouraged."""

    def test_a_plain_relative_path_is_kept(self) -> None:
        self.assertEqual(
            GitClientMixin._safe_restore_pathspecs(['src/app.js']), ['src/app.js'],
        )

    def test_repeated_separators_are_normalised(self) -> None:
        self.assertEqual(
            GitClientMixin._safe_restore_pathspecs(['src//a.js']), ['src/a.js'],
        )

    def test_backslashes_are_accepted_for_windows_callers(self) -> None:
        self.assertEqual(
            GitClientMixin._safe_restore_pathspecs(['src\\a.js']), ['src/a.js'],
        )

    def test_everything_that_could_widen_the_revert_is_dropped(self) -> None:
        for path in (
            '.', './', '..', '../etc/passwd', 'a/../b', 'a/./b',
            '/etc/passwd', '~/secrets', 'C:/Windows/system32',
            '*', '**', 'src/*.js', 'src/a[12].js', 'a?.js',
            ':(glob)**', ':!keep', ':/', '-rf', '',
        ):
            with self.subTest(path=path):
                self.assertEqual(
                    GitClientMixin._safe_restore_pathspecs([path]), [],
                    f'{path!r} must never reach git as a pathspec',
                )

    def test_one_bad_path_does_not_smuggle_itself_in_beside_a_good_one(self) -> None:
        self.assertEqual(
            GitClientMixin._safe_restore_pathspecs(['src/a.js', '.', '../x']),
            ['src/a.js'],
        )


class _Client(GitClientMixin):
    """Minimal harness: capture argv instead of running git.

    ``diff_output`` is what ``git diff --name-only <base>`` returns,
    ``untracked_output`` what ``git ls-files --others`` returns, and
    ``missing_at_base`` the paths ``git cat-file -e <base>:<path>`` fails
    for (i.e. files added on this branch).
    """

    def __init__(
        self, diff_output: str = '', untracked_output: str = '',
        missing_at_base=(),
    ) -> None:
        self.diff_output = diff_output
        self.untracked_output = untracked_output
        self.missing_at_base = set(missing_at_base)
        self.calls: list[list[str]] = []
        self.removed: list[str] = []

    def _git_stdout(self, local_path, args, failure_message, repository=None):
        self.calls.append(args)
        if args and args[0] == 'diff':
            return self.diff_output
        if args and args[0] == 'ls-files':
            return self.untracked_output
        return ''

    def _run_git(self, local_path, args, failure_message, repository=None):
        self.calls.append(args)
        return mock.Mock(returncode=0, stdout='', stderr='')

    def _run_git_subprocess(self, local_path, args, repository=None):
        # ``cat-file -e <base>:<path>`` — presence probe.
        target = args[-1].split(':', 1)[-1]
        code = 1 if target in self.missing_at_base else 0
        return mock.Mock(returncode=code, stdout='', stderr='')


class RestorePathsTests(unittest.TestCase):
    """Anchored on the base branch, not on HEAD.

    The bug this fixes: the Files tree colours a file changed-vs-BASE,
    which includes changes the agent already committed on the task branch.
    Discard restored from HEAD, so for a committed change it was a no-op —
    the operator clicked and watched the file stay marked.
    """

    def _restore(self, argv_calls):
        return next((c for c in argv_calls if c and c[0] == 'restore'), None)

    def test_it_discards_against_the_supplied_base(self) -> None:
        client = _Client(diff_output='src/app.js')
        result = client.restore_paths('/repo', ['src/app.js'], source='origin/main')
        self.assertEqual(result, ['src/app.js'])
        restore = self._restore(client.calls)
        self.assertIn('--source=origin/main', restore)

    def test_a_change_already_COMMITTED_on_the_branch_is_discarded(self) -> None:
        # git status would call this clean; the diff against base does not.
        client = _Client(diff_output='src/app.js')
        self.assertEqual(
            client.restore_paths('/repo', ['src/app.js'], source='origin/main'),
            ['src/app.js'],
        )
        self.assertIsNotNone(self._restore(client.calls))

    def test_the_comparison_is_against_base_not_HEAD(self) -> None:
        client = _Client(diff_output='src/app.js')
        client.restore_paths('/repo', ['src/app.js'], source='origin/main')
        diff = next(c for c in client.calls if c and c[0] == 'diff')
        self.assertIn('origin/main', diff)

    def test_the_pathspec_always_follows_a_double_dash(self) -> None:
        client = _Client(diff_output='src/app.js')
        client.restore_paths('/repo', ['src/app.js'], source='origin/main')
        restore = self._restore(client.calls)
        self.assertIn('--', restore)
        self.assertEqual(restore[restore.index('--') + 1:], ['src/app.js'])

    def test_a_file_matching_base_reports_nothing_discarded(self) -> None:
        # "Nothing to discard" must stay distinguishable from "discarded",
        # or the UI reports a success that did not happen.
        client = _Client(diff_output='', untracked_output='')
        self.assertEqual(client.restore_paths('/repo', ['src/app.js']), [])
        self.assertIsNone(self._restore(client.calls))

    def test_an_untracked_file_IS_a_change_against_base(self) -> None:
        # Relative to the base branch a file the agent created is a change,
        # and the tree colours it as one — so discard has to clear it.
        client = _Client(untracked_output='src/new.js', missing_at_base={'src/new.js'})
        self.assertEqual(
            client.restore_paths('/repo', ['src/new.js'], source='origin/main'),
            ['src/new.js'],
        )

    def test_a_file_ADDED_on_this_branch_is_deleted_not_restored(self) -> None:
        # git restore has nothing to restore it from and would fail.
        # Discarding the change to a new file means removing it.
        client = _Client(diff_output='src/new.js', missing_at_base={'src/new.js'})
        with mock.patch('os.remove') as removed:
            client.restore_paths('/repo', ['src/new.js'], source='origin/main')
        self.assertTrue(removed.called)
        self.assertIsNone(self._restore(client.calls))

    def test_a_deleted_new_file_is_also_unstaged(self) -> None:
        # Otherwise a staged addition survives the file being gone and
        # reappears in the diff as a deletion.
        client = _Client(diff_output='src/new.js', missing_at_base={'src/new.js'})
        with mock.patch('os.remove'):
            client.restore_paths('/repo', ['src/new.js'], source='origin/main')
        self.assertTrue(any(c and c[0] == 'rm' for c in client.calls))

    def test_a_staged_change_is_discarded_too(self) -> None:
        client = _Client(diff_output='src/app.js')
        client.restore_paths('/repo', ['src/app.js'], source='origin/main')
        restore = self._restore(client.calls)
        self.assertIn('--staged', restore)
        self.assertIn('--worktree', restore)

    def test_it_defaults_to_HEAD_when_no_base_is_supplied(self) -> None:
        client = _Client(diff_output='src/app.js')
        client.restore_paths('/repo', ['src/app.js'])
        self.assertIn('--source=HEAD', self._restore(client.calls))

    def test_an_unusable_path_raises_instead_of_touching_something_else(self) -> None:
        client = _Client(diff_output='x')
        for path in ('.', '../etc/passwd', '*'):
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    client.restore_paths('/repo', [path])


if __name__ == '__main__':
    unittest.main()
