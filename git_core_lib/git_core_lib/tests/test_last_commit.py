"""The commit a clone is on and when it was made, read from real repositories.

It moves only when work is committed: an edit that is not committed leaves it
where it was, and a branch with no commit at all is an error, never ''.
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from git_core_lib.git_core_lib.client.git_client import GitClientMixin


class _Client(GitClientMixin):
    def __init__(self) -> None:
        self.logger = logging.getLogger('test.last_commit')


def _git(cwd: Path, *args: str, env: dict | None = None) -> str:
    return subprocess.run(
        ['git', *args], cwd=str(cwd), check=True, capture_output=True, text=True, env=env,
    ).stdout.strip()


class LastCommitTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name).resolve() / 'repo'
        self.repo.mkdir()
        _git(self.repo, 'init', '-q', '-b', 'master')
        _git(self.repo, 'config', 'user.email', 't@example.com')
        _git(self.repo, 'config', 'user.name', 'test')
        self.client = _Client()

    def _commit(self, name: str, *, at: int) -> str:
        (self.repo / name).write_text(name)
        _git(self.repo, 'add', name)
        env = dict(os.environ, GIT_COMMITTER_DATE=f'@{at} +0000', GIT_AUTHOR_DATE=f'@{at} +0000')
        _git(self.repo, 'commit', '-q', '-m', name, env=env)
        return _git(self.repo, 'rev-parse', 'HEAD')

    def test_the_checked_out_commit_and_its_committer_time(self) -> None:
        commit = self._commit('a.txt', at=1_700_000_000)
        self.assertEqual(self.client.last_commit(str(self.repo)), (commit, 1_700_000_000))

    def test_a_new_commit_moves_it_and_an_uncommitted_edit_does_not(self) -> None:
        first = self._commit('a.txt', at=1_700_000_000)
        (self.repo / 'a.txt').write_text('edited, not committed')
        self.assertEqual(self.client.last_commit(str(self.repo))[0], first)
        second = self._commit('b.txt', at=1_700_000_100)
        self.assertEqual(self.client.last_commit(str(self.repo)), (second, 1_700_000_100))

    def test_a_branch_with_no_commit_cannot_answer(self) -> None:
        with self.assertRaises(RuntimeError):
            self.client.last_commit(str(self.repo))

    def test_an_answer_it_cannot_read_raises(self) -> None:
        with patch.object(_Client, '_git_stdout', return_value='abc not-a-time'):
            with self.assertRaisesRegex(RuntimeError, 'failed to parse the last commit'):
                self.client.last_commit(str(self.repo))


if __name__ == '__main__':
    unittest.main()
