"""Real git: a clone whose own commits were rewritten after a push, or left mid-rebase.

When the agent may run the history of its branch itself (commit --amend,
rebase, reset), two things can be true of a clone that never were when only
the host ran git:

* the branch no longer contains what was already pushed. The push is then
  rejected as non-fast-forward, and the old recovery — fetch + rebase onto
  origin — replays the rewritten commits on top of the originals: the same
  change twice, or a conflict against itself. The push must be refused and
  say why instead.
* a rebase / merge / cherry-pick stopped on a conflict and is not finished.

Every test drives real repositories (a bare origin, a working clone, and a
second clone standing in for someone else) — the command-sequence doubles in
test_flow.py cannot tell "the remote moved" from "the branch was rewritten".
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from git_core_lib.git_core_lib.client.git_client import GitClientMixin
from git_core_lib.git_core_lib.tests.test_git_client_real import _git, _GitOnly

BRANCH = 'TASK-1'


@unittest.skipUnless(shutil.which('git'), 'git binary not available on this system')
class _RealRepos(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix='git-rewritten-')
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.origin = root / 'origin.git'
        self.work = root / 'work'
        self.origin.mkdir()
        self.work.mkdir()
        _git(self.origin, 'init', '--bare', '--initial-branch', 'main')
        _git(self.work, 'init', '--initial-branch', 'main')
        _git(self.work, 'remote', 'add', 'origin', str(self.origin))
        self._commit(self.work, 'README.md', 'base\n', 'baseline')
        _git(self.work, 'push', '-u', 'origin', 'main')
        _git(self.work, 'checkout', '-b', BRANCH)
        self.client = _GitOnly()

    def _commit(self, clone: Path, name: str, text: str, message: str) -> str:
        (clone / name).write_text(text, encoding='utf-8')
        _git(clone, 'add', name)
        _git(clone, 'commit', '-m', message)
        return _git(clone, 'rev-parse', 'HEAD').stdout.strip()

    def _origin_log(self) -> list[str]:
        return _git(self.origin, 'log', '--format=%s', BRANCH).stdout.split('\n')[:-1]

    def _push(self) -> None:
        self.client._push_branch(str(self.work), BRANCH)


class RewrittenAfterPushTests(_RealRepos):
    def setUp(self) -> None:
        super().setUp()
        self.pushed = self._commit(self.work, 'a.txt', 'one\n', 'first change')
        self._push()

    def test_an_amended_commit_is_not_pushed(self) -> None:
        _git(self.work, 'commit', '--amend', '-m', 'first change, reworded')

        with self.assertRaises(RuntimeError) as caught:
            self._push()

        message = str(caught.exception)
        self.assertIn(self.pushed[:12], message)
        self.assertIn('rewritten after the last push', message)
        self.assertIn(f'git rebase origin/{BRANCH}', message)
        # Nothing moved on the remote, and nothing was rebased locally.
        self.assertEqual(self._origin_log(), ['first change', 'baseline'])
        self.assertEqual(
            _git(self.work, 'log', '-1', '--format=%s').stdout.strip(),
            'first change, reworded',
        )

    def test_a_reset_past_the_pushed_commit_is_not_pushed(self) -> None:
        _git(self.work, 'reset', '--hard', 'HEAD~1')
        self._commit(self.work, 'b.txt', 'two\n', 'a different change')

        with self.assertRaisesRegex(RuntimeError, 'rewritten after the last push'):
            self._push()
        self.assertEqual(self._origin_log(), ['first change', 'baseline'])

    def test_a_squash_of_pushed_and_new_commits_is_not_pushed(self) -> None:
        self._commit(self.work, 'b.txt', 'two\n', 'second change')
        _git(self.work, 'reset', '--soft', 'HEAD~2')
        _git(self.work, 'commit', '-m', 'both changes, squashed')

        with self.assertRaisesRegex(RuntimeError, 'rewritten after the last push'):
            self._push()

    def test_putting_the_pushed_commits_back_lets_it_push(self) -> None:
        # The way out the message names: rebase onto origin/<branch>.
        _git(self.work, 'reset', '--hard', 'HEAD~1')
        self._commit(self.work, 'b.txt', 'two\n', 'a different change')
        _git(self.work, 'rebase', f'origin/{BRANCH}')

        self._push()

        self.assertEqual(
            self._origin_log(), ['a different change', 'first change', 'baseline'],
        )

    def test_new_commits_on_top_of_the_pushed_one_push_normally(self) -> None:
        self._commit(self.work, 'b.txt', 'two\n', 'second change')

        self._push()

        self.assertEqual(self._origin_log(), ['second change', 'first change', 'baseline'])

    def test_a_dry_run_does_not_check(self) -> None:
        # The connectivity probe asks "may I push?", not "should this push?".
        _git(self.work, 'commit', '--amend', '-m', 'reworded')
        with self.assertRaises(RuntimeError) as caught:
            self.client._push_branch(str(self.work), BRANCH, dry_run=True)
        self.assertNotIn('rewritten after the last push', str(caught.exception))
        self.assertIn('non-fast-forward', str(caught.exception))


class RemoteMovedOnTests(_RealRepos):
    """Someone else pushed to the branch: the old fetch + rebase recovery stands."""

    def setUp(self) -> None:
        super().setUp()
        self._commit(self.work, 'a.txt', 'one\n', 'mine, pushed')
        self._push()
        other = Path(self._tmp.name) / 'other'
        _git(Path(self._tmp.name), 'clone', '--branch', BRANCH, str(self.origin), str(other))
        self._commit(other, 'theirs.txt', 'theirs\n', 'theirs')
        _git(other, 'push', 'origin', BRANCH)
        self._commit(self.work, 'b.txt', 'two\n', 'mine, new')

    def test_rejected_push_is_rebased_and_retried(self) -> None:
        self._push()
        self.assertEqual(
            self._origin_log(), ['mine, new', 'theirs', 'mine, pushed', 'baseline'],
        )

    def test_a_fetch_that_brought_their_commit_is_not_called_a_rewrite(self) -> None:
        # The tracking ref now names a commit this branch never had as its tip.
        _git(self.work, 'fetch', 'origin', f'{BRANCH}:refs/remotes/origin/{BRANCH}')
        self.assertEqual(self.client.rewritten_pushed_commit(str(self.work), BRANCH), '')
        self._push()
        self.assertEqual(self._origin_log()[0], 'mine, new')


class NothingToCompareTests(_RealRepos):
    def test_a_branch_never_pushed_has_nothing_rewritten(self) -> None:
        self._commit(self.work, 'a.txt', 'one\n', 'first')
        self.assertEqual(self.client.rewritten_pushed_commit(str(self.work), BRANCH), '')

    def test_an_unreadable_reflog_is_not_a_rewrite(self) -> None:
        self._commit(self.work, 'a.txt', 'one\n', 'first change')
        self._push()
        _git(self.work, 'commit', '--amend', '-m', 'reworded')
        _git(self.work, 'reflog', 'expire', '--expire=all', '--all')
        # No record of former tips: cannot prove a rewrite, so the old
        # behaviour (rejection → fetch + rebase) is left to happen.
        self.assertEqual(self.client.rewritten_pushed_commit(str(self.work), BRANCH), '')


    def test_a_pushed_branch_with_no_local_branch_left_is_not_a_rewrite(self) -> None:
        self._commit(self.work, 'a.txt', 'one\n', 'first change')
        self._push()
        _git(self.work, 'checkout', 'main')
        _git(self.work, 'branch', '-D', BRANCH)
        self.assertEqual(self.client.rewritten_pushed_commit(str(self.work), BRANCH), '')


class OperationInProgressTests(_RealRepos):
    def _conflict_setup(self) -> None:
        self._commit(self.work, 'a.txt', 'branch\n', 'on the branch')
        _git(self.work, 'checkout', 'main')
        self._commit(self.work, 'a.txt', 'main\n', 'on main')
        _git(self.work, 'checkout', BRANCH)

    def test_a_clean_clone_has_nothing_in_progress(self) -> None:
        self.assertEqual(self.client.git_operation_in_progress(str(self.work)), '')

    def test_a_stopped_rebase(self) -> None:
        self._conflict_setup()
        _git(self.work, 'rebase', 'main', check=False)
        self.assertEqual(self.client.git_operation_in_progress(str(self.work)), 'rebase')
        _git(self.work, 'rebase', '--abort')
        self.assertEqual(self.client.git_operation_in_progress(str(self.work)), '')

    def test_a_stopped_merge(self) -> None:
        self._conflict_setup()
        _git(self.work, 'merge', 'main', check=False)
        self.assertEqual(self.client.git_operation_in_progress(str(self.work)), 'merge')

    def test_a_stopped_cherry_pick(self) -> None:
        self._conflict_setup()
        _git(self.work, 'cherry-pick', 'main', check=False)
        self.assertEqual(self.client.git_operation_in_progress(str(self.work)), 'cherry-pick')

    def test_a_stopped_revert(self) -> None:
        self._commit(self.work, 'a.txt', 'one\n', 'one')
        self._commit(self.work, 'a.txt', 'two\n', 'two')
        _git(self.work, 'revert', '--no-edit', 'HEAD~1', check=False)
        self.assertEqual(self.client.git_operation_in_progress(str(self.work)), 'revert')

    def test_a_stopped_am(self) -> None:
        self._conflict_setup()
        patch_text = _git(self.work, 'format-patch', '-1', 'main', '--stdout').stdout
        patch_file = Path(self._tmp.name) / 'change.patch'
        patch_file.write_text(patch_text, encoding='utf-8')
        _git(self.work, 'am', str(patch_file), check=False)
        self.assertEqual(self.client.git_operation_in_progress(str(self.work)), 'am')

    def test_a_bisect(self) -> None:
        self._commit(self.work, 'a.txt', 'one\n', 'one')
        _git(self.work, 'bisect', 'start')
        self.assertEqual(self.client.git_operation_in_progress(str(self.work)), 'bisect')

    def test_a_linked_worktree_reads_its_own_git_dir(self) -> None:
        # ``rev-parse --git-dir`` answers an ABSOLUTE path in a linked
        # worktree (``<repo>/.git/worktrees/<name>``), not ``.git``.
        self._conflict_setup()
        tree = Path(self._tmp.name) / 'tree'
        _git(self.work, 'worktree', 'add', '-b', 'other', str(tree), BRANCH)
        self.assertEqual(self.client.git_operation_in_progress(str(tree)), '')
        _git(tree, 'merge', 'main', check=False)
        self.assertEqual(self.client.git_operation_in_progress(str(tree)), 'merge')
        self.assertEqual(self.client.git_operation_in_progress(str(self.work)), '')

    def test_a_folder_that_is_not_a_repository(self) -> None:
        self.assertEqual(self.client.git_operation_in_progress(self._tmp.name), '')


class IndexLockAgeTests(_RealRepos):
    def test_a_fresh_lock_is_left_for_the_git_that_may_hold_it(self) -> None:
        # A git started from inside the clone (``git commit`` while its hook
        # runs) is invisible to the process check: only its age protects it.
        lock = self.work / '.git' / 'index.lock'
        lock.write_text('live', encoding='utf-8')

        self.assertFalse(self.client._clear_stale_git_index_lock(str(self.work)))
        self.assertTrue(lock.is_file())

    def test_an_old_lock_is_cleared(self) -> None:
        lock = self.work / '.git' / 'index.lock'
        lock.write_text('stale', encoding='utf-8')
        old = time.time() - GitClientMixin.GIT_INDEX_LOCK_LIVE_SECONDS - 1
        os.utime(lock, (old, old))

        self.assertTrue(self.client._clear_stale_git_index_lock(str(self.work)))
        self.assertFalse(lock.is_file())



if __name__ == '__main__':
    unittest.main()
