"""The git steps a branch rename is made of, against real repositories.

A remote has no rename: a commit is pushed under the new name, and the old
branch is deleted. Both pushes carry a lease, so neither can move or delete a
branch someone else pushed to in the meantime.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from git_core_lib.git_core_lib.client.git_client import GitClientMixin


class _Client(GitClientMixin):
    def __init__(self) -> None:
        self.logger = logging.getLogger('test.branch_rename_helpers')


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ['git', *args], cwd=str(cwd), check=True, capture_output=True, text=True,
    ).stdout.strip()


class _Repositories(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve()
        self.remote = root / 'remote.git'
        _git(root, 'init', '-q', '--bare', '-b', 'master', str(self.remote))
        self.clone = root / 'clone'
        self.other = root / 'other'
        for path in (self.clone, self.other):
            _git(root, 'clone', '-q', str(self.remote), path.name)
            _git(path, 'config', 'user.email', 't@example.com')
            _git(path, 'config', 'user.name', 'test')
        self.first = self._commit(self.clone, 'one')
        _git(self.clone, 'push', '-q', '-u', 'origin', 'HEAD:refs/heads/old')
        _git(self.clone, 'branch', '-q', '-m', 'old')
        _git(self.clone, 'branch', '-q', '--set-upstream-to=origin/old', 'old')
        self.client = _Client()
        self.repository = SimpleNamespace(remote_url=str(self.remote))

    @staticmethod
    def _commit(path: Path, message: str) -> str:
        _git(path, 'commit', '-q', '--allow-empty', '-m', message)
        return _git(path, 'rev-parse', 'HEAD')

    def _on_remote(self) -> dict[str, str]:
        lines = _git(self.remote, 'for-each-ref', '--format=%(refname:short) %(objectname)', 'refs/heads')
        return dict(line.split() for line in lines.splitlines())


class RemoteBranchCommitTests(_Repositories):
    def test_the_commit_a_remote_branch_points_at(self) -> None:
        self.assertEqual(self.client.remote_branch_commit(str(self.clone), 'old'), self.first)

    def test_a_branch_not_there_is_empty(self) -> None:
        self.assertEqual(self.client.remote_branch_commit(str(self.clone), 'missing'), '')
        self.assertFalse(self.client.remote_has_branch(str(self.clone), 'missing'))

    def test_only_the_exact_branch_counts(self) -> None:
        # ``ls-remote`` matches by tail: ``team/old`` also ends in ``old``.
        _git(self.other, 'fetch', '-q', 'origin')
        _git(self.other, 'push', '-q', 'origin', f'{self.first}:refs/heads/team/old')
        _git(self.clone, 'push', '-q', 'origin', ':refs/heads/old')
        self.assertEqual(self.client.remote_branch_commit(str(self.clone), 'old'), '')

    def test_a_remote_that_cannot_be_asked_raises(self) -> None:
        _git(self.clone, 'remote', 'set-url', 'origin', str(self.remote.parent / 'gone.git'))
        with self.assertRaises(RuntimeError):
            self.client.remote_branch_commit(str(self.clone), 'old')


class BranchContainsTests(_Repositories):
    def test_a_commit_in_the_branch(self) -> None:
        self._commit(self.clone, 'two')
        self.assertTrue(self.client.branch_contains(str(self.clone), 'old', self.first))

    def test_a_commit_this_clone_does_not_have(self) -> None:
        _git(self.other, 'fetch', '-q', 'origin')
        _git(self.other, 'checkout', '-q', '-b', 'theirs', 'origin/old')
        theirs = self._commit(self.other, 'theirs')
        self.assertFalse(self.client.branch_contains(str(self.clone), 'old', theirs))


class PushCommitAsNewBranchTests(_Repositories):
    def test_the_commit_lands_under_the_new_name(self) -> None:
        self._commit(self.clone, 'not pushed')
        self.client.push_commit_as_new_branch(str(self.clone), self.first, 'new', self.repository)
        self.assertEqual(self._on_remote()['new'], self.first)
        self.assertEqual(_git(self.clone, 'rev-parse', 'refs/remotes/origin/new'), self.first)

    def test_a_branch_someone_pushed_there_is_never_moved(self) -> None:
        # Even a fast-forward: the empty lease means "must not exist".
        _git(self.other, 'fetch', '-q', 'origin')
        _git(self.other, 'push', '-q', 'origin', f'{self.first}:refs/heads/new')
        later = self._commit(self.clone, 'later')
        with self.assertRaises(RuntimeError):
            self.client.push_commit_as_new_branch(str(self.clone), later, 'new', self.repository)
        self.assertEqual(self._on_remote()['new'], self.first)

    def test_a_redirected_remote_is_refused_before_anything_is_sent(self) -> None:
        elsewhere = SimpleNamespace(remote_url=str(self.remote.parent / 'configured.git'))
        with self.assertRaisesRegex(RuntimeError, 'refusing to use remote'):
            self.client.push_commit_as_new_branch(str(self.clone), self.first, 'new', elsewhere)
        self.assertNotIn('new', self._on_remote())


class DeleteRemoteBranchTests(_Repositories):
    def test_the_branch_goes_while_it_is_what_was_checked(self) -> None:
        self.client.delete_remote_branch(str(self.clone), 'old', self.first, self.repository)
        self.assertNotIn('old', self._on_remote())

    def test_anything_pushed_since_stays(self) -> None:
        _git(self.other, 'fetch', '-q', 'origin')
        _git(self.other, 'checkout', '-q', '-b', 'theirs', 'origin/old')
        theirs = self._commit(self.other, 'theirs')
        _git(self.other, 'push', '-q', 'origin', 'theirs:old')
        with self.assertRaises(RuntimeError):
            self.client.delete_remote_branch(str(self.clone), 'old', self.first, self.repository)
        self.assertEqual(self._on_remote()['old'], theirs)

    def test_a_redirected_remote_is_refused(self) -> None:
        elsewhere = SimpleNamespace(remote_url=str(self.remote.parent / 'configured.git'))
        with self.assertRaisesRegex(RuntimeError, 'refusing to use remote'):
            self.client.delete_remote_branch(str(self.clone), 'old', self.first, elsewhere)
        self.assertIn('old', self._on_remote())


class LocalBranchTests(_Repositories):
    def test_rename_and_track_the_new_remote_branch(self) -> None:
        self.client.push_commit_as_new_branch(str(self.clone), self.first, 'new', self.repository)
        self.client.rename_local_branch(str(self.clone), 'old', 'new')
        self.client.set_upstream_branch(str(self.clone), 'new')
        self.assertTrue(self.client.has_local_branch(str(self.clone), 'new'))
        self.assertFalse(self.client.has_local_branch(str(self.clone), 'old'))
        self.assertEqual(_git(self.clone, 'rev-parse', '--abbrev-ref', 'new@{upstream}'), 'origin/new')

    def test_rename_refuses_a_name_already_taken(self) -> None:
        _git(self.clone, 'branch', 'new')
        with self.assertRaises(RuntimeError):
            self.client.rename_local_branch(str(self.clone), 'old', 'new')

    def test_tracking_a_remote_branch_that_is_not_there_raises(self) -> None:
        with self.assertRaises(RuntimeError):
            self.client.set_upstream_branch(str(self.clone), 'old-no-such')


if __name__ == '__main__':
    unittest.main()
