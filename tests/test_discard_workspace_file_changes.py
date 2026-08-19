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
    """Minimal harness: capture argv instead of running git."""

    def __init__(self, status_output: str = '') -> None:
        self.status_output = status_output
        self.calls: list[list[str]] = []

    def _git_stdout(self, local_path, args, failure_message, repository=None):
        self.calls.append(args)
        return self.status_output

    def _run_git(self, local_path, args, failure_message, repository=None):
        self.calls.append(args)
        return mock.Mock(returncode=0, stdout='', stderr='')


class RestorePathsTests(unittest.TestCase):
    def test_restores_a_dirty_file(self) -> None:
        client = _Client(status_output=' M src/app.js')
        self.assertEqual(client.restore_paths('/repo', ['src/app.js']), ['src/app.js'])
        restore = client.calls[-1]
        self.assertEqual(restore[0], 'restore')
        self.assertIn('--', restore)
        self.assertEqual(restore[restore.index('--') + 1:], ['src/app.js'])

    def test_the_pathspec_always_follows_a_double_dash(self) -> None:
        # Without ``--`` a path that looks like a flag or a ref would be
        # interpreted as one.
        client = _Client(status_output=' M src/app.js')
        client.restore_paths('/repo', ['src/app.js'])
        self.assertIn('--', client.calls[-1])

    def test_a_clean_file_reports_nothing_discarded(self) -> None:
        # "Nothing to discard" must be distinguishable from "discarded",
        # or the UI reports a success that did not happen.
        client = _Client(status_output='')
        self.assertEqual(client.restore_paths('/repo', ['src/app.js']), [])
        self.assertTrue(all(call[0] != 'restore' for call in client.calls))

    def test_an_untracked_file_is_left_alone(self) -> None:
        # It has no committed state to restore to, and deleting a file the
        # operator never committed would be a surprise, not a discard.
        client = _Client(status_output='?? src/new.js')
        self.assertEqual(client.restore_paths('/repo', ['src/new.js']), [])

    def test_a_renamed_file_restores_its_NEW_path(self) -> None:
        client = _Client(status_output='R  src/old.js -> src/new.js')
        self.assertEqual(client.restore_paths('/repo', ['src/new.js']), ['src/new.js'])

    def test_a_staged_change_is_discarded_too(self) -> None:
        # --staged AND --worktree: discarding a file the agent had already
        # staged must actually clear it, not leave the staged copy.
        client = _Client(status_output='M  src/app.js')
        client.restore_paths('/repo', ['src/app.js'])
        restore = client.calls[-1]
        self.assertIn('--staged', restore)
        self.assertIn('--worktree', restore)

    def test_an_unusable_path_raises_instead_of_touching_something_else(self) -> None:
        client = _Client(status_output=' M x')
        for path in ('.', '../etc/passwd', '*'):
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    client.restore_paths('/repo', [path])


if __name__ == '__main__':
    unittest.main()
