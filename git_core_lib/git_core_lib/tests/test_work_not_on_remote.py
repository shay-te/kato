"""What deleting a clone would lose, read from real repositories.

Uncommitted changes on any branch, and commits on any local branch that no
remote-tracking branch contains. Pushed work is not counted.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
import unittest
from pathlib import Path

from git_core_lib.git_core_lib.client.git_client import GitClientMixin


class _Client(GitClientMixin):
    def __init__(self) -> None:
        self.logger = logging.getLogger('test.work_not_on_remote')


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ['git', *args], cwd=str(cwd), check=True, capture_output=True, text=True,
    ).stdout.strip()


class WorkNotOnRemoteTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve()
        self.remote = root / 'remote.git'
        _git(root, 'init', '-q', '--bare', '-b', 'master', str(self.remote))
        self.clone = root / 'clone'
        _git(root, 'clone', '-q', str(self.remote), 'clone')
        _git(self.clone, 'config', 'user.email', 't@example.com')
        _git(self.clone, 'config', 'user.name', 'test')
        (self.clone / 'a.txt').write_text('one\n')
        _git(self.clone, 'add', 'a.txt')
        _git(self.clone, 'commit', '-q', '-m', 'one')
        _git(self.clone, 'push', '-q', '-u', 'origin', 'master')
        _git(self.clone, 'checkout', '-q', '-b', 'TASK-1')
        self.client = _Client()

    def _work(self) -> dict:
        return self.client.work_not_on_remote(str(self.clone))

    def _commit(self, message: str) -> None:
        _git(self.clone, 'commit', '-q', '--allow-empty', '-m', message)

    def test_a_fresh_task_branch_has_nothing_to_lose(self) -> None:
        self.assertEqual(self._work(), {'changed_files': [], 'unpushed_commits': 0})

    def test_edited_and_new_files_are_listed(self) -> None:
        (self.clone / 'a.txt').write_text('two\n')
        (self.clone / 'new.txt').write_text('new\n')
        work = self._work()
        self.assertEqual(sorted(work['changed_files']), ['a.txt', 'new.txt'])
        self.assertEqual(work['unpushed_commits'], 0)

    def test_an_unstaged_edit_keeps_its_whole_path(self) -> None:
        # Porcelain's first column is data (" M a.txt"): never strip it away.
        (self.clone / 'a.txt').write_text('two\n')
        self.assertEqual(self._work()['changed_files'], ['a.txt'])

    def test_commits_never_pushed_are_counted(self) -> None:
        self._commit('two')
        self._commit('three')
        self.assertEqual(self._work()['unpushed_commits'], 2)

    def test_pushed_commits_are_not_counted(self) -> None:
        self._commit('two')
        _git(self.clone, 'push', '-q', '-u', 'origin', 'TASK-1')
        self.assertEqual(self._work(), {'changed_files': [], 'unpushed_commits': 0})

    def test_commits_ahead_of_a_pushed_branch_are_counted(self) -> None:
        self._commit('two')
        _git(self.clone, 'push', '-q', '-u', 'origin', 'TASK-1')
        self._commit('three')
        self.assertEqual(self._work()['unpushed_commits'], 1)

    def test_a_branch_that_is_not_checked_out_still_counts(self) -> None:
        # The clone sits on master; the task's commit is on its own branch.
        self._commit('two')
        _git(self.clone, 'checkout', '-q', 'master')
        self.assertEqual(self._work()['unpushed_commits'], 1)

    def test_uncommitted_changes_on_another_branch_count(self) -> None:
        _git(self.clone, 'checkout', '-q', 'master')
        (self.clone / 'a.txt').write_text('two\n')
        self.assertEqual(self._work()['changed_files'], ['a.txt'])

    def test_a_folder_git_cannot_read_raises(self) -> None:
        not_a_repo = self.clone.parent / 'plain'
        not_a_repo.mkdir()
        with self.assertRaises(RuntimeError):
            self.client.work_not_on_remote(str(not_a_repo))


if __name__ == '__main__':
    unittest.main()
