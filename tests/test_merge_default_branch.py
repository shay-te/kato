"""``Merge master`` — fetch + merge the default branch into a task
branch so the (git-blocked) agent can resolve conflicts by editing
files.

Two layers:
  * RepositoryService.merge_default_branch_into_clone — preflight
    refusals (mocked) + a real on-disk git repo for the clean-merge
    and conflict paths (the conflict path is the whole point: markers
    must be LEFT in the tree, not aborted).
  * agent_service.publish.merge_default_branch_for_task — aggregation across
    repos (mocked repo-service outcomes).
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from kato_core_lib.data_layers.service.repository_service import (
    RepositoryService,
)
from tests.utils import build_test_cfg


def _make_service():
    return RepositoryService(build_test_cfg(), 3)


def _git(cwd, *args):
    subprocess.run(
        ['git', *args], cwd=str(cwd), check=True,
        capture_output=True, text=True,
    )


def _build_repo_with_diverged_default(tmp: Path):
    """Create an origin + a clone whose task branch is behind the
    default branch, with a change that will/​won't conflict depending
    on the file written. Returns (clone_path, repository_ns)."""
    origin = tmp / 'origin.git'
    work = tmp / 'seed'
    work.mkdir()
    _git(work, 'init', '-q')
    _git(work, 'config', 'user.email', 't@example.com')
    _git(work, 'config', 'user.name', 'Test')
    _git(work, 'checkout', '-q', '-b', 'main')
    (work / 'shared.txt').write_text('base\n', encoding='utf-8')
    _git(work, 'add', '-A')
    _git(work, 'commit', '-q', '-m', 'base')
    _git(work, 'clone', '-q', '--bare', str(work), str(origin))
    _git(work, 'remote', 'add', 'origin', str(origin))
    _git(work, 'push', '-q', 'origin', 'main')

    clone = tmp / 'clone'
    _git(tmp, 'clone', '-q', str(origin), str(clone))
    _git(clone, 'config', 'user.email', 't@example.com')
    _git(clone, 'config', 'user.name', 'Test')
    _git(clone, 'checkout', '-q', '-b', 'feat/x', 'main')
    _git(clone, 'commit', '-q', '--allow-empty', '-m', 'task work')

    # Advance main on origin so feat/x is behind by one commit.
    _git(work, 'checkout', '-q', 'main')
    (work / 'shared.txt').write_text('CHANGED ON MAIN\n', encoding='utf-8')
    _git(work, 'add', '-A')
    _git(work, 'commit', '-q', '-m', 'main moved')
    _git(work, 'push', '-q', 'origin', 'main')

    repo = SimpleNamespace(id='client', local_path=str(clone),
                           destination_branch='main')
    return clone, repo



class InterruptedCloneRepairTests(unittest.TestCase):
    """``git clone`` killed mid-fetch leaves a folder holding only ``.git``.

    HEAD still points at the placeholder ``refs/heads/.invalid`` that clone
    writes before fetching, so every git command answers:

        fatal: ambiguous argument 'HEAD': unknown revision or path not in the
        working tree.

    It cannot self-heal on the normal path: every later pass sees ``.git``
    and agrees the repo is already on disk. The operator clicked Merge five
    times before realising one repo of twenty-five was in this state, so kato
    now REPAIRS it rather than reporting it.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.service = _make_service()

    def _origin(self) -> Path:
        """A bare origin with one commit on ``main``."""
        work = self.tmp / 'seed'
        work.mkdir()
        _git(work, 'init', '-q')
        _git(work, 'config', 'user.email', 't@example.com')
        _git(work, 'config', 'user.name', 'Test')
        _git(work, 'checkout', '-q', '-b', 'main')
        (work / 'shared.txt').write_text('base\n', encoding='utf-8')
        _git(work, 'add', '-A')
        _git(work, 'commit', '-q', '-m', 'base')
        origin = self.tmp / 'origin.git'
        _git(work, 'clone', '-q', '--bare', str(work), str(origin))
        return origin

    def _interrupted_clone(self, at: Path) -> Path:
        """The exact on-disk shape a killed ``git clone`` leaves behind.

        Including the ``tmp_pack_*`` scratch file. That detail is the whole
        reason the repair sat dead: a fetch streams into
        ``objects/pack/tmp_pack_XXXXXX`` and renames it only once the pack
        is complete, so EVERY clone killed mid-download has one — and the
        emptiness test counted it as real objects and refused to repair.
        The operator kept getting git's raw fatal from a repo whose only
        content was this 0-byte scratch file.
        """
        pack = at / '.git' / 'objects' / 'pack'
        pack.mkdir(parents=True)
        (pack / 'tmp_pack_HJydSJ').write_bytes(b'')
        (at / '.git' / 'objects' / 'info').mkdir()
        (at / '.git' / 'refs' / 'heads').mkdir(parents=True)
        (at / '.git' / 'refs' / 'tags').mkdir()
        (at / '.git' / 'HEAD').write_text(
            'ref: refs/heads/.invalid\n', encoding='utf-8',
        )
        return at

    def test_a_finished_pack_is_real_content_and_is_never_deleted(self) -> None:
        """The line between "scratch file" and "your repository".

        Only ``tmp_*`` is ignored. A completed ``pack-<sha>.pack`` means the
        objects arrived, so the clone answers "not empty" and keeps every
        byte — the emptiness test is what licenses removing the directory.
        """
        clone = self._interrupted_clone(self.tmp / 'has-a-pack')
        pack = clone / '.git' / 'objects' / 'pack'
        (pack / 'pack-0123456789abcdef.pack').write_bytes(b'PACK')

        self.assertFalse(self.service._clone_is_empty_of_objects(clone))

    def test_the_tmp_pack_scratch_file_does_not_count_as_content(self) -> None:
        # The regression itself, pinned on the predicate directly.
        clone = self._interrupted_clone(self.tmp / 'scratch-only')
        self.assertTrue(self.service._clone_is_empty_of_objects(clone))

    def test_the_clone_is_re_fetched_and_the_merge_then_runs(self) -> None:
        origin = self._origin()
        clone = self._interrupted_clone(self.tmp / 'event-core-lib')
        repo = SimpleNamespace(
            id='event-core-lib', local_path=str(clone),
            remote_url=str(origin), destination_branch='main',
        )

        out = self.service.merge_default_branch_into_clone(repo, 'feat/x')

        # Repaired and merged — NOT a refusal the operator has to act on.
        self.assertTrue(out['merged'], out)
        # The objects actually arrived and the working tree exists this time.
        self.assertTrue((clone / 'shared.txt').is_file())
        # ...on the task branch, which the interrupted clone never had. Without
        # recreating it the repair "succeeds" and the merge then refuses with
        # wrong_branch_checked_out — a second dead end for the same fault.
        self.assertEqual(self.service._current_branch(str(clone)), 'feat/x')

    def test_a_repair_that_cannot_run_reports_plainly(self) -> None:
        # No remote_url — kato cannot re-fetch. The operator still gets a
        # sentence naming the repo and the real cause, never git's
        # "ambiguous argument 'HEAD'" for a state they cannot act on.
        clone = self._interrupted_clone(self.tmp / 'event-core-lib')
        repo = SimpleNamespace(
            id='event-core-lib', local_path=str(clone),
            remote_url='', destination_branch='main',
        )

        out = self.service.merge_default_branch_into_clone(repo, 'feat/x')

        self.assertFalse(out['merged'])
        self.assertEqual(out['reason'], 'incomplete_clone')
        self.assertIn('event-core-lib', out['detail'])
        self.assertIn('never finished downloading', out['detail'])
        self.assertNotIn('ambiguous argument', out['detail'])

    def test_a_clone_holding_real_objects_is_never_deleted(self) -> None:
        """The guard on the destructive step, pinned.

        The repair REMOVES the directory. It is licensed by the emptiness
        test alone — a repo with objects whose HEAD is merely broken keeps
        every file and gets reported instead.
        """
        origin = self._origin()
        clone = self.tmp / 'client'
        _git(self.tmp, 'clone', '-q', str(origin), str(clone))
        (clone / '.git' / 'HEAD').write_text(
            'ref: refs/heads/.invalid\n', encoding='utf-8',
        )
        repo = SimpleNamespace(
            id='client', local_path=str(clone),
            remote_url=str(origin), destination_branch='main',
        )

        out = self.service.merge_default_branch_into_clone(repo, 'feat/x')

        self.assertFalse(out['merged'])
        self.assertEqual(out['reason'], 'branch_lookup_failed')
        self.assertTrue((clone / 'shared.txt').is_file())


class CloneCheckoutFailedTests(unittest.TestCase):
    """``warning: Clone succeeded, but checkout failed.``

    git exits NON-ZERO for this, but the clone is complete: every object is
    present and the branch is set — only the working tree is empty. Reported
    from a Windows machine where the checkout step died mid-clone:

        error: cannot spawn : No such file or directory
        fatal: unable to parse commit 731628754213b64137e077c3518be6fb21de68f0
        warning: Clone succeeded, but checkout failed.

    kato raised on the non-zero exit, so the repair that does exactly what
    git's own advice says (``git restore --source=HEAD :/``) was never
    reached. The operator saw a repo with every file gone — "for some repos
    he just deletes all the files in the repo, I have to manually reset all
    the changes to bring the files back" — and, because the clone raised,
    branch prep never ran either: "all the repos are still on master and not
    on the task branch".
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.service = _make_service()

    def _origin(self) -> Path:
        work = self.tmp / 'seed'
        work.mkdir()
        _git(work, 'init', '-q')
        _git(work, 'config', 'user.email', 't@example.com')
        _git(work, 'config', 'user.name', 'Test')
        _git(work, 'checkout', '-q', '-b', 'main')
        (work / 'kept.txt').write_text('real content\n', encoding='utf-8')
        _git(work, 'add', '-A')
        _git(work, 'commit', '-q', '-m', 'base')
        origin = self.tmp / 'origin.git'
        _git(work, 'clone', '-q', '--bare', str(work), str(origin))
        return origin

    def _clone_then_empty_the_worktree(self, origin: Path, at: Path) -> None:
        """The exact on-disk state git leaves: complete .git, no files."""
        _git(self.tmp, 'clone', '-q', str(origin), str(at))
        for entry in at.iterdir():
            if entry.name != '.git':
                entry.unlink()

    def test_a_failed_checkout_is_restored_instead_of_failing_the_task(self) -> None:
        origin = self._origin()
        target = self.tmp / 'achievement-core-lib'
        repo = SimpleNamespace(
            id='achievement-core-lib', local_path=str(target),
            remote_url=str(origin), destination_branch='main',
        )

        # Stand in for git's "clone succeeded, but checkout failed": leave the
        # real repository behind, then report the non-zero exit.
        real_run_git = self.service._run_git
        calls = {'n': 0}

        def fake_run_git(cwd, args, message, repository=None, **kwargs):
            if args and args[0] == 'clone':
                calls['n'] += 1
                self._clone_then_empty_the_worktree(origin, target)
                raise RuntimeError(f'{message}: warning: Clone succeeded, but checkout failed.')
            return real_run_git(cwd, args, message, repository, **kwargs)

        self.service._run_git = fake_run_git
        self.service.ensure_clone(repo, target)

        self.assertEqual(calls['n'], 1)
        # The files are BACK — not left for the operator to restore by hand.
        self.assertTrue((target / 'kept.txt').is_file())
        self.assertEqual(
            (target / 'kept.txt').read_text(encoding='utf-8'), 'real content\n',
        )

    def test_a_genuinely_failed_clone_still_raises(self) -> None:
        # The rescue must not swallow a real failure — a task whose repo never
        # arrived has to fail loudly, not proceed against an empty folder.
        target = self.tmp / 'never-arrived'
        repo = SimpleNamespace(
            id='never-arrived', local_path=str(target),
            remote_url='https://example.invalid/x.git', destination_branch='main',
        )

        def fake_run_git(cwd, args, message, repository=None, **kwargs):
            raise RuntimeError(f'{message}: could not read from remote')

        self.service._run_git = fake_run_git
        with self.assertRaises(RuntimeError) as caught:
            self.service.ensure_clone(repo, target)
        # ...and the ORIGINAL cause survives, not a second error from the
        # rescue attempt.
        self.assertIn('could not read from remote', str(caught.exception))

    def test_a_directory_with_files_but_NO_git_is_not_mistaken_for_success(self) -> None:
        """The check the rescue turns on.

        A clone that died early can leave a folder holding partial content
        and no ``.git`` at all. "Are there files?" alone would read that as a
        healthy repository and let the task proceed against a folder git
        knows nothing about — no branch, no remote, nothing to push.
        """
        target = self.tmp / 'half-written'
        target.mkdir()
        (target / 'stray.txt').write_text('partial\n', encoding='utf-8')
        repo = SimpleNamespace(
            id='half-written', local_path=str(target),
            remote_url='https://example.invalid/x.git', destination_branch='main',
        )

        def fake_run_git(cwd, args, message, repository=None, **kwargs):
            raise RuntimeError(f'{message}: died early')

        self.service._run_git = fake_run_git
        with self.assertRaises(RuntimeError):
            self.service.ensure_clone(repo, target)

    def test_the_rescue_never_touches_a_directory_that_holds_files(self) -> None:
        """The destructive-step guard, on the new path.

        ``_restore_unchecked_out_clone`` runs ``checkout -f``. It is licensed
        ONLY by the directory holding nothing but ``.git`` — reaching it with
        the agent's uncommitted work present is how 41 files were destroyed
        once before.
        """
        origin = self._origin()
        target = self.tmp / 'has-work'
        _git(self.tmp, 'clone', '-q', str(origin), str(target))
        (target / 'kept.txt').write_text('OPERATOR EDIT\n', encoding='utf-8')
        repo = SimpleNamespace(
            id='has-work', local_path=str(target),
            remote_url=str(origin), destination_branch='main',
        )

        self.service._restore_unchecked_out_clone(repo, target)

        self.assertEqual(
            (target / 'kept.txt').read_text(encoding='utf-8'), 'OPERATOR EDIT\n',
        )


class KatoMovesItsOwnCloneOntoTheTaskBranchTests(unittest.TestCase):
    """A workspace clone on the wrong branch is kato's to fix.

    UNA-2417, once the destroyed clone had been restored: every repo action
    answered "objective_love_core_lib: workspace is on 'master', expected
    'UNA-2417' — checkout first". Nobody chose that branch — a fresh clone
    lands on the remote's default — so the operator was being told to do git
    surgery inside kato's own workspace to unblock a button.

    Real git throughout: the whole question is what happens to the working
    tree, and a mock cannot answer it.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.service = _make_service()
        self.clone, self.repo = _build_repo_with_diverged_default(self.tmp)
        _git(self.clone, 'checkout', '-q', 'main')  # where a re-clone lands

    def _branch(self) -> str:
        return subprocess.run(
            ['git', 'rev-parse', '--abbrev-ref', 'HEAD'], cwd=str(self.clone),
            capture_output=True, text=True,
        ).stdout.strip()

    def test_merge_moves_the_clone_onto_the_task_branch_and_runs(self) -> None:
        out = self.service.merge_default_branch_into_clone(self.repo, 'feat/x')
        self.assertTrue(out['merged'], out)
        self.assertEqual(self._branch(), 'feat/x')

    def test_uncommitted_work_survives_the_move(self) -> None:
        # The recovery carries a dirty tree across; the merge then saves it as
        # a WIP commit. Either way the operator's file is still on disk.
        (self.clone / 'agent_work.py').write_text('AGENT OUTPUT\n', encoding='utf-8')
        self.service.merge_default_branch_into_clone(self.repo, 'feat/x')
        self.assertEqual(
            (self.clone / 'agent_work.py').read_text(encoding='utf-8'),
            'AGENT OUTPUT\n',
        )
        self.assertEqual(self._branch(), 'feat/x')

    def test_a_clone_with_its_own_commits_there_is_still_refused(self) -> None:
        # Moving commits between branches is a rebase decision, not kato's to
        # make silently. The refusal stays — but it now says WHY.
        (self.clone / 'local.txt').write_text('local\n', encoding='utf-8')
        _git(self.clone, 'add', '-A')
        _git(self.clone, 'commit', '-q', '-m', 'work on the wrong branch')

        out = self.service.merge_default_branch_into_clone(self.repo, 'feat/x')

        self.assertFalse(out['merged'])
        self.assertEqual(out['reason'], 'wrong_branch_checked_out')
        self.assertIn('rebase or cherry-pick', out['detail'])
        self.assertNotIn('checkout first', out['detail'])
        self.assertEqual(self._branch(), 'main', 'the clone was moved anyway')

    def test_pull_moves_it_too(self) -> None:
        # Same dead end, same fix: Pull refused instead of checking out.
        self.service.pull_workspace_clone(self.repo, 'feat/x')
        self.assertEqual(self._branch(), 'feat/x')


class MergePreflightTests(unittest.TestCase):
    """Mocked refusals — never reach a real git repo."""

    def setUp(self) -> None:
        self.service = _make_service()
        self.service._validate_local_path = MagicMock()

    def test_no_local_path(self) -> None:
        repo = SimpleNamespace(id='c', local_path='')
        out = self.service.merge_default_branch_into_clone(repo, 'feat/x')
        self.assertFalse(out['merged'])
        self.assertEqual(out['reason'], 'no_local_path')

    def test_wrong_branch_checked_out(self) -> None:
        repo = SimpleNamespace(id='c', local_path='/x')
        with patch.object(Path, 'is_dir', return_value=True), \
             patch.object(self.service, '_current_branch',
                          return_value='other'):
            out = self.service.merge_default_branch_into_clone(repo, 'feat/x')
        self.assertEqual(out['reason'], 'wrong_branch_checked_out')

    def test_dirty_working_tree_saves_wip_and_proceeds(self) -> None:
        # Refusing on a dirty tree turned "Merge master" into a chore (the
        # agent almost always has in-progress edits). The service now saves
        # them as a WIP commit and continues into the fetch+merge.
        repo = SimpleNamespace(id='c', local_path='/x')
        git_calls = []

        def record_git(local_path, args, message, repository=None):
            git_calls.append(args)

        with patch.object(Path, 'is_dir', return_value=True), \
             patch.object(self.service, '_current_branch',
                          return_value='feat/x'), \
             patch.object(self.service, '_working_tree_status',
                          return_value=' M file.py'), \
             patch.object(self.service, 'destination_branch',
                          return_value='main'), \
             patch.object(self.service, '_run_git',
                          side_effect=record_git), \
             patch.object(self.service, '_git_reference_exists',
                          return_value=True), \
             patch.object(self.service, '_left_right_commit_counts',
                          return_value=(0, 0)):
            out = self.service.merge_default_branch_into_clone(repo, 'feat/x')

        # The WIP was staged + committed before anything else…
        self.assertEqual(git_calls[0], ['add', '-A'])
        self.assertEqual(git_calls[1][:2], ['commit', '-m'])
        self.assertIn('WIP', git_calls[1][2])
        # …and the flow carried on (fetch ran, outcome reports the WIP).
        self.assertIn(['fetch', 'origin', '--prune'], git_calls)
        self.assertTrue(out['wip_committed'])
        self.assertTrue(out['merged'])


class MergeRealGitTests(unittest.TestCase):
    """Real on-disk git: clean merge AND the conflict path."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.service = _make_service()
        self.service._validate_local_path = MagicMock()

    def test_clean_merge_brings_default_branch_in(self) -> None:
        clone, repo = _build_repo_with_diverged_default(self.tmp)
        # feat/x didn't touch shared.txt → clean fast-content merge.
        out = self.service.merge_default_branch_into_clone(repo, 'feat/x')
        self.assertTrue(out['merged'], out)
        self.assertTrue(out['updated'])
        self.assertEqual(out['default_branch'], 'main')
        self.assertEqual(
            (clone / 'shared.txt').read_text(encoding='utf-8'),
            'CHANGED ON MAIN\n',
        )

    def test_conflict_leaves_markers_in_tree_not_aborted(self) -> None:
        clone, repo = _build_repo_with_diverged_default(self.tmp)
        # Make feat/x edit the SAME line main changed → real conflict.
        (clone / 'shared.txt').write_text('CHANGED ON FEAT\n',
                                          encoding='utf-8')
        _git(clone, 'add', '-A')
        _git(clone, 'commit', '-q', '-m', 'feat edits shared')
        out = self.service.merge_default_branch_into_clone(repo, 'feat/x')
        self.assertFalse(out['merged'])
        self.assertTrue(out['conflicts'])
        self.assertIn('shared.txt', out['conflicted_files'])
        # The whole point: markers + MERGE_HEAD must be LEFT so the
        # agent can resolve them; the merge was NOT aborted.
        self.assertTrue((clone / '.git' / 'MERGE_HEAD').exists())
        self.assertIn(
            '<<<<<<<',
            (clone / 'shared.txt').read_text(encoding='utf-8'),
        )

    def test_finalize_merge_commits_once_the_agent_resolved_the_conflict(self) -> None:
        clone, repo = _build_repo_with_diverged_default(self.tmp)
        (clone / 'shared.txt').write_text('CHANGED ON FEAT\n', encoding='utf-8')
        _git(clone, 'add', '-A')
        _git(clone, 'commit', '-q', '-m', 'feat edits shared')
        self.service.merge_default_branch_into_clone(repo, 'feat/x')
        # Sanity: mid-merge with markers + MERGE_HEAD.
        self.assertTrue((clone / '.git' / 'MERGE_HEAD').exists())

        # Before resolution the finalize is a NO-OP (markers remain).
        pending = self.service.finalize_merge_if_resolved(repo)
        self.assertFalse(pending['finalized'])
        self.assertEqual(pending['reason'], 'conflicts_remain')
        self.assertIn('shared.txt', pending['unresolved_files'])
        self.assertTrue((clone / '.git' / 'MERGE_HEAD').exists())

        # The agent resolves by editing the file (no ``git add`` — sandbox).
        (clone / 'shared.txt').write_text('RESOLVED\n', encoding='utf-8')

        out = self.service.finalize_merge_if_resolved(repo)
        self.assertTrue(out['finalized'], out)
        self.assertEqual(out['repository_id'], 'client')
        # The merge is committed: MERGE_HEAD gone, tree clean.
        self.assertFalse((clone / '.git' / 'MERGE_HEAD').exists())
        self.assertEqual(
            subprocess.run(
                ['git', 'status', '--porcelain'], cwd=str(clone),
                capture_output=True, text=True, check=True,
            ).stdout.strip(),
            '',
        )
        # It IS a merge commit (two parents) so the diff base collapses to
        # origin/main → the operator sees only the branch's work.
        parents = subprocess.run(
            ['git', 'rev-list', '--parents', '-n', '1', 'HEAD'], cwd=str(clone),
            capture_output=True, text=True, check=True,
        ).stdout.split()
        self.assertEqual(len(parents), 3, 'expected a merge commit (2 parents)')

    def test_finalize_merge_is_a_noop_when_no_merge_pending(self) -> None:
        clone, repo = _build_repo_with_diverged_default(self.tmp)
        out = self.service.finalize_merge_if_resolved(repo)
        self.assertFalse(out['finalized'])
        self.assertEqual(out['reason'], 'no_pending_merge')

    def test_dirty_tree_wip_commits_then_merges_for_real(self) -> None:
        clone, repo = _build_repo_with_diverged_default(self.tmp)
        # Agent left uncommitted work in the tree (non-conflicting file).
        (clone / 'agent-notes.txt').write_text('in progress\n', encoding='utf-8')

        out = self.service.merge_default_branch_into_clone(repo, 'feat/x')

        self.assertTrue(out['merged'])
        self.assertTrue(out['updated'])
        self.assertTrue(out['wip_committed'])
        # The default branch's change arrived…
        self.assertEqual(
            (clone / 'shared.txt').read_text(encoding='utf-8'),
            'CHANGED ON MAIN\n',
        )
        # …the agent's work survived as a commit (clean tree, file present).
        self.assertEqual(
            subprocess.run(
                ['git', 'status', '--porcelain'], cwd=str(clone),
                capture_output=True, text=True, check=True,
            ).stdout.strip(),
            '',
        )
        self.assertTrue((clone / 'agent-notes.txt').is_file())
        log = subprocess.run(
            ['git', 'log', '--oneline', '-4'], cwd=str(clone),
            capture_output=True, text=True, check=True,
        ).stdout
        self.assertIn('WIP: save in-progress work', log)

    def test_merges_a_repo_that_COMMITS_its_build_output(self) -> None:
        """A tracked ``build/`` is part of the repo, not a disposable artifact.

        The publication exclusions unstage anything under a top-level
        {build,dist,out,coverage,target} — a bare NAME match — and the merge
        path leaves those paths dirty on the justification that "they don't
        exist on the default branch, so they cannot conflict". Some repos
        genuinely commit their build output (``ob-love-admin-client`` has 446
        files under ``build/`` on master), and there git refuses outright:

            error: Your local changes to the following files would be
            overwritten by merge: build/asset-manifest.json, build/index.html

        Every "Merge master" click failed with nothing the operator could do.
        """
        tmp = self.tmp
        origin, work = tmp / 'o2.git', tmp / 'seed2'
        work.mkdir()
        _git(work, 'init', '-q')
        _git(work, 'config', 'user.email', 't@example.com')
        _git(work, 'config', 'user.name', 'Test')
        _git(work, 'checkout', '-q', '-b', 'main')
        # The repo COMMITS its build output — tracked on the default branch.
        (work / 'shared.txt').write_text('base\n', encoding='utf-8')
        (work / 'build').mkdir()
        (work / 'build' / 'index.html').write_text('built v1\n', encoding='utf-8')
        _git(work, 'add', '-A')
        _git(work, 'commit', '-q', '-m', 'base + build output')
        _git(work, 'clone', '-q', '--bare', str(work), str(origin))
        _git(work, 'remote', 'add', 'origin', str(origin))

        clone = tmp / 'clone2'
        _git(tmp, 'clone', '-q', str(origin), str(clone))
        _git(clone, 'config', 'user.email', 't@example.com')
        _git(clone, 'config', 'user.name', 'Test')
        _git(clone, 'checkout', '-q', '-b', 'feat/y', 'main')
        _git(clone, 'commit', '-q', '--allow-empty', '-m', 'task work')

        # main moves, and it moves the BUILD OUTPUT too.
        (work / 'build' / 'index.html').write_text('built v2\n', encoding='utf-8')
        _git(work, 'add', '-A')
        _git(work, 'commit', '-q', '-m', 'main rebuilt')
        _git(work, 'push', '-q', 'origin', 'main')

        # A local rebuild leaves the tracked file dirty — the real situation.
        (clone / 'build' / 'index.html').write_text('built locally\n', encoding='utf-8')

        repo = SimpleNamespace(id='client', local_path=str(clone),
                               destination_branch='main')
        out = self.service.merge_default_branch_into_clone(repo, 'feat/y')

        # A REAL three-way merge happened. Before, git aborted before merging
        # anything — "would be overwritten by merge … Aborting" — and the
        # operator had no move: the file kato left dirty was the blocker.
        self.assertNotIn('would be overwritten', str(out))
        self.assertTrue(out['conflicts'], out)
        self.assertEqual(out['conflicted_files'], ['build/index.html'])

        # The local rebuild was preserved as a commit rather than left dirty…
        log = subprocess.run(
            ['git', 'log', '--oneline', '-3'], cwd=str(clone),
            capture_output=True, text=True, check=True,
        ).stdout
        self.assertIn('WIP: save in-progress work', log)
        # …and the conflict is in the tree with markers, which is kato's
        # documented contract: the agent resolves it by editing files.
        self.assertIn(
            '<<<<<<<',
            (clone / 'build' / 'index.html').read_text(encoding='utf-8'),
        )

    def test_untracked_build_output_is_still_excluded_from_the_wip_commit(self) -> None:
        """The exclusion still holds for a repo that does NOT commit build/.

        Only tracked-ness changed. A repo whose build output is genuinely
        disposable must keep it out of the WIP commit — otherwise the fix for
        the tracked case would start shipping build artifacts into PRs.
        """
        clone, repo = _build_repo_with_diverged_default(self.tmp)
        (clone / 'build').mkdir()
        (clone / 'build' / 'bundle.js').write_text('generated\n', encoding='utf-8')
        (clone / 'agent-notes.txt').write_text('real work\n', encoding='utf-8')

        out = self.service.merge_default_branch_into_clone(repo, 'feat/x')

        self.assertTrue(out['merged'], out)
        tracked = subprocess.run(
            ['git', 'ls-files', 'build'], cwd=str(clone),
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        self.assertEqual(tracked, '', 'disposable build output must not be committed')
        self.assertTrue((clone / 'build' / 'bundle.js').is_file())

    def test_wip_commit_never_tracks_the_validation_report(self) -> None:
        """The report is the PR description, not a file to ship.

        Regression: ``add -A`` swept ``validation_report.md`` into the WIP
        commit, and once TRACKED the publish path could no longer strip it
        (its reset+clean only reach untracked / merely-staged files). One
        report rode three WIP commits onto a branch and into the PR.
        """
        clone, repo = _build_repo_with_diverged_default(self.tmp)
        (clone / 'agent-notes.txt').write_text('in progress\n', encoding='utf-8')
        (clone / 'validation_report.md').write_text('# report\n', encoding='utf-8')

        out = self.service.merge_default_branch_into_clone(repo, 'feat/x')

        self.assertTrue(out['merged'])
        self.assertTrue(out['wip_committed'])
        tracked = subprocess.run(
            ['git', 'ls-files'], cwd=str(clone),
            capture_output=True, text=True, check=True,
        ).stdout
        self.assertNotIn('validation_report.md', tracked)
        # Real work still saved…
        self.assertIn('agent-notes.txt', tracked)
        # …and the report survives on disk for publication to consume.
        self.assertTrue((clone / 'validation_report.md').is_file())

    def test_a_report_only_tree_makes_no_wip_commit(self) -> None:
        # Nothing to save once the report is excluded — committing an empty
        # index would fail, so the merge must simply proceed without one.
        clone, repo = _build_repo_with_diverged_default(self.tmp)
        (clone / 'validation_report.md').write_text('# report\n', encoding='utf-8')

        out = self.service.merge_default_branch_into_clone(repo, 'feat/x')

        self.assertTrue(out['merged'])
        self.assertFalse(out['wip_committed'])
        self.assertTrue((clone / 'validation_report.md').is_file())
        self.assertNotIn('validation_report.md', subprocess.run(
            ['git', 'ls-files'], cwd=str(clone),
            capture_output=True, text=True, check=True,
        ).stdout)

    def test_already_up_to_date_is_a_noop(self) -> None:
        clone, repo = _build_repo_with_diverged_default(self.tmp)
        # First merge brings main in cleanly...
        self.service.merge_default_branch_into_clone(repo, 'feat/x')
        # ...second merge has nothing left to do.
        out = self.service.merge_default_branch_into_clone(repo, 'feat/x')
        self.assertTrue(out['merged'])
        self.assertFalse(out['updated'])
        self.assertEqual(out['commits_merged'], 0)


class AgentAggregationTests(unittest.TestCase):
    """merge_default_branch_for_task rolls per-repo outcomes up."""

    def _service(self):
        from kato_core_lib.data_layers.service.task_publish_service import (
    TaskPublishService,
)
        # Real construction of the publish service — it owns merging now, so
        # the collaborators it needs are named rather than poked into a
        # half-built AgentService.
        repository_service = MagicMock()
        repository_service.build_branch_name.return_value = 'feat/x'
        return TaskPublishService(
            repository_service=repository_service,
            task_service=MagicMock(),
            task_state_service=MagicMock(),
            task_publisher=MagicMock(),
            workspace_manager=MagicMock(),
            logger=MagicMock(),
        )

    def test_empty_task_id(self) -> None:
        svc = self._service()
        out = svc.merge_default_branch_for_task('  ')
        self.assertFalse(out['merged'])
        self.assertEqual(out['error'], 'empty task id')

    def test_conflicts_surface_with_files(self) -> None:
        svc = self._service()
        repo = SimpleNamespace(id='client')
        svc._repository_service.merge_default_branch_into_clone.return_value = {
            'merged': False, 'conflicts': True, 'default_branch': 'main',
            'conflicted_files': ['a.py', 'b.py'],
        }
        with patch.object(
            svc, '_resolve_publish_context',
            return_value=([repo], 'feat/x', SimpleNamespace(id='T-1')),
        ):
            out = svc.merge_default_branch_for_task('T-1')
        self.assertTrue(out['has_conflicts'])
        self.assertEqual(
            out['conflicted_repositories'][0]['conflicted_files'],
            ['a.py', 'b.py'],
        )

    def test_clean_merge_aggregates(self) -> None:
        svc = self._service()
        repo = SimpleNamespace(id='client')
        svc._repository_service.merge_default_branch_into_clone.return_value = {
            'merged': True, 'updated': True, 'commits_merged': 3,
            'default_branch': 'main',
        }
        with patch.object(
            svc, '_resolve_publish_context',
            return_value=([repo], 'feat/x', SimpleNamespace(id='T-1')),
        ):
            out = svc.merge_default_branch_for_task('T-1')
        self.assertTrue(out['merged'])
        self.assertFalse(out['has_conflicts'])
        self.assertEqual(
            out['merged_repositories'][0]['commits_merged'], 3,
        )

    def test_finalize_resolved_merges_aggregates_finalized_and_pending(self) -> None:
        svc = self._service()
        done = SimpleNamespace(id='resolved')
        still = SimpleNamespace(id='still-conflicted')
        svc._repository_service.finalize_merge_if_resolved.side_effect = [
            {'finalized': True, 'repository_id': 'resolved'},
            {'finalized': False, 'reason': 'conflicts_remain',
             'unresolved_files': ['x.py']},
        ]
        with patch.object(
            svc, '_resolve_publish_context',
            return_value=([done, still], 'feat/x', SimpleNamespace(id='T-1')),
        ):
            out = svc.finalize_resolved_merges_for_task('T-1')
        self.assertEqual(out['finalized_repositories'], ['resolved'])
        self.assertEqual(out['pending_repositories'], ['still-conflicted'])

    def test_finalize_resolved_merges_isolates_a_raising_repo(self) -> None:
        svc = self._service()
        repo = SimpleNamespace(id='boom')
        svc._repository_service.finalize_merge_if_resolved.side_effect = (
            RuntimeError('git exploded')
        )
        with patch.object(
            svc, '_resolve_publish_context',
            return_value=([repo], 'feat/x', SimpleNamespace(id='T-1')),
        ):
            out = svc.finalize_resolved_merges_for_task('T-1')
        # The raise is swallowed (best-effort read path) and logged.
        self.assertEqual(out['finalized_repositories'], [])
        svc.logger.exception.assert_called()

    def test_finalize_resolved_merges_empty_task_id_is_a_noop(self) -> None:
        svc = self._service()
        out = svc.finalize_resolved_merges_for_task('   ')
        self.assertEqual(out['finalized_repositories'], [])
        svc._repository_service.finalize_merge_if_resolved.assert_not_called()

    def test_no_workspace_context_returns_error(self) -> None:
        # _resolve_publish_context yields no repos (clone gone /
        # task never provisioned) → explicit error, no merge attempt.
        svc = self._service()
        with patch.object(
            svc, '_resolve_publish_context',
            return_value=([], None, None),
        ):
            out = svc.merge_default_branch_for_task('T-1')
        self.assertFalse(out['merged'])
        self.assertEqual(out['task_id'], 'T-1')
        self.assertEqual(out['error'], 'no workspace context for this task')
        svc._repository_service.merge_default_branch_into_clone.assert_not_called()

    def test_repo_merge_exception_is_isolated_as_failed(self) -> None:
        # One repo raising must not abort the run — it lands in
        # failed_repositories with the error string.
        svc = self._service()
        repo = SimpleNamespace(id='client')
        svc._repository_service.merge_default_branch_into_clone.side_effect = (
            RuntimeError('git exploded')
        )
        with patch.object(
            svc, '_resolve_publish_context',
            return_value=([repo], 'feat/x', SimpleNamespace(id='T-1')),
        ):
            out = svc.merge_default_branch_for_task('T-1')
        self.assertFalse(out['merged'])
        self.assertEqual(out['failed_repositories'][0]['repository_id'], 'client')
        self.assertIn('git exploded', out['failed_repositories'][0]['error'])
        svc.logger.exception.assert_called()

    def test_already_contains_default_branch_is_skipped(self) -> None:
        # merged=True but updated=False → branch already had the
        # default branch; reported as an already_up_to_date skip.
        svc = self._service()
        repo = SimpleNamespace(id='client')
        svc._repository_service.merge_default_branch_into_clone.return_value = {
            'merged': True, 'updated': False,
        }
        with patch.object(
            svc, '_resolve_publish_context',
            return_value=([repo], 'feat/x', SimpleNamespace(id='T-1')),
        ):
            out = svc.merge_default_branch_for_task('T-1')
        skipped = out['skipped_repositories'][0]
        self.assertEqual(skipped['repository_id'], 'client')
        self.assertEqual(skipped['reason'], 'already_up_to_date')

    def test_unmergeable_outcome_is_skipped_with_reason(self) -> None:
        # Neither merged nor conflicts (e.g. preflight refused) →
        # skipped with the outcome's own reason/detail surfaced.
        svc = self._service()
        repo = SimpleNamespace(id='client')
        svc._repository_service.merge_default_branch_into_clone.return_value = {
            'merged': False, 'conflicts': False,
            'reason': 'dirty_tree', 'detail': 'uncommitted changes',
        }
        with patch.object(
            svc, '_resolve_publish_context',
            return_value=([repo], 'feat/x', SimpleNamespace(id='T-1')),
        ):
            out = svc.merge_default_branch_for_task('T-1')
        skipped = out['skipped_repositories'][0]
        self.assertEqual(skipped['reason'], 'dirty_tree')
        self.assertEqual(skipped['detail'], 'uncommitted changes')


# ---------------------------------------------------------------------------
# Coverage for defensive branches in _merge_preflight + merge_default_branch_into_clone
# ---------------------------------------------------------------------------


class MergePreflightDefensiveBranchTests(unittest.TestCase):
    """Cover the remaining ``return fail(...)`` branches in
    ``_merge_preflight`` that the original tests didn't reach."""

    def setUp(self) -> None:
        self.service = _make_service()
        self.service._validate_local_path = MagicMock()

    def test_not_a_git_repo(self) -> None:
        repo = SimpleNamespace(id='c', local_path='/no/git/here')
        with patch.object(Path, 'is_dir', return_value=False):
            out = self.service.merge_default_branch_into_clone(repo, 'feat/x')
        self.assertEqual(out['reason'], 'not_a_git_repo')

    def test_no_branch_argument(self) -> None:
        repo = SimpleNamespace(id='c', local_path='/x')
        with patch.object(Path, 'is_dir', return_value=True):
            out = self.service.merge_default_branch_into_clone(repo, '   ')
        self.assertEqual(out['reason'], 'no_branch')

    def test_current_branch_lookup_failure(self) -> None:
        repo = SimpleNamespace(id='c', local_path='/x')
        with patch.object(Path, 'is_dir', return_value=True), \
             patch.object(self.service, '_current_branch',
                          side_effect=RuntimeError('git rev-parse failed')):
            out = self.service.merge_default_branch_into_clone(repo, 'feat/x')
        self.assertEqual(out['reason'], 'branch_lookup_failed')
        self.assertIn('git rev-parse failed', out['detail'])

    def test_status_check_failure(self) -> None:
        # The dirty-tree probe now runs AFTER preflight (it feeds the
        # WIP-commit step), so the default branch must resolve first.
        repo = SimpleNamespace(id='c', local_path='/x')
        with patch.object(Path, 'is_dir', return_value=True), \
             patch.object(self.service, '_current_branch',
                          return_value='feat/x'), \
             patch.object(self.service, 'destination_branch',
                          return_value='main'), \
             patch.object(self.service, '_working_tree_status',
                          side_effect=RuntimeError('git status failed')):
            out = self.service.merge_default_branch_into_clone(repo, 'feat/x')
        self.assertEqual(out['reason'], 'status_check_failed')

    def test_default_branch_unknown(self) -> None:
        repo = SimpleNamespace(id='c', local_path='/x')
        with patch.object(Path, 'is_dir', return_value=True), \
             patch.object(self.service, '_current_branch',
                          return_value='feat/x'), \
             patch.object(self.service, '_working_tree_status',
                          return_value=''), \
             patch.object(self.service, 'destination_branch',
                          side_effect=ValueError('no default branch')):
            out = self.service.merge_default_branch_into_clone(repo, 'feat/x')
        self.assertEqual(out['reason'], 'default_branch_unknown')


class MergeDefaultBranchPostPreflightBranchTests(unittest.TestCase):
    """Defensive branches AFTER ``_merge_preflight`` returns OK —
    fetch failure, remote-lookup failure, missing remote default,
    commit-count failure, merge-fail-with-abort."""

    def setUp(self) -> None:
        self.service = _make_service()
        # All preflight checks pass; default branch is 'master'.
        self.service._merge_preflight = MagicMock(
            return_value={'default_branch': 'master'},
        )

    def test_fetch_failure_surfaces_fetch_failed(self) -> None:
        repo = SimpleNamespace(id='c', local_path='/x')
        with patch.object(self.service, '_working_tree_status',
                          return_value=''), \
             patch.object(self.service, '_run_git',
                          side_effect=RuntimeError('network down')):
            out = self.service.merge_default_branch_into_clone(repo, 'feat/x')
        self.assertEqual(out['reason'], 'fetch_failed')
        self.assertIn('network down', out['detail'])

    def test_remote_lookup_exception_surfaces_remote_lookup_failed(self) -> None:
        repo = SimpleNamespace(id='c', local_path='/x')
        with patch.object(self.service, '_run_git'), \
             patch.object(self.service, '_git_reference_exists',
                          side_effect=RuntimeError('rev-parse failed')):
            out = self.service.merge_default_branch_into_clone(repo, 'feat/x')
        self.assertEqual(out['reason'], 'remote_lookup_failed')

    def test_remote_default_missing(self) -> None:
        repo = SimpleNamespace(id='c', local_path='/x')
        with patch.object(self.service, '_run_git'), \
             patch.object(self.service, '_git_reference_exists',
                          return_value=False):
            out = self.service.merge_default_branch_into_clone(repo, 'feat/x')
        self.assertEqual(out['reason'], 'remote_default_missing')

    def test_commit_count_failure(self) -> None:
        repo = SimpleNamespace(id='c', local_path='/x')
        with patch.object(self.service, '_run_git'), \
             patch.object(self.service, '_git_reference_exists',
                          return_value=True), \
             patch.object(self.service, '_left_right_commit_counts',
                          side_effect=RuntimeError('count failed')):
            out = self.service.merge_default_branch_into_clone(repo, 'feat/x')
        self.assertEqual(out['reason'], 'commit_count_failed')

    def test_non_zero_merge_with_no_conflict_aborts(self) -> None:
        # Lines 769-775: ``git merge`` returns non-zero but
        # ``_unmerged_paths`` is empty — so the merge failed for some
        # other reason (refusing for an unrelated cause). We must
        # abort the merge and return reason='merge_failed'.
        repo = SimpleNamespace(id='c', local_path='/x')
        # Behind-count > 0 so we actually attempt the merge.
        with patch.object(self.service, '_run_git'), \
             patch.object(self.service, '_git_reference_exists',
                          return_value=True), \
             patch.object(self.service, '_left_right_commit_counts',
                          return_value=(0, 3)), \
             patch.object(self.service, '_run_git_subprocess',
                          return_value=SimpleNamespace(
                              returncode=1, stderr='merge bailed',
                              stdout='',
                          )), \
             patch.object(self.service, '_unmerged_paths', return_value=[]):
            out = self.service.merge_default_branch_into_clone(repo, 'feat/x')
        self.assertEqual(out['reason'], 'merge_failed')
        self.assertIn('merge bailed', out['detail'])


class UnmergedPathsBranchTest(unittest.TestCase):
    """Line 784: ``_unmerged_paths`` returns [] when git exits non-zero."""

    def test_returns_empty_list_on_git_failure(self) -> None:
        service = _make_service()
        with patch.object(
            service, '_run_git_subprocess',
            return_value=SimpleNamespace(returncode=1, stdout='', stderr='x'),
        ):
            self.assertEqual(service._unmerged_paths('/x'), [])


class GetRepositoryRaisesWhenAllFallbacksMissTests(unittest.TestCase):
    """Line 179: the final ``raise ValueError`` in
    ``RepositoryService.get_repository`` when both the inventory
    lookup AND the direct-folder fallback miss."""

    def test_raises_when_inventory_and_direct_lookup_both_miss(self) -> None:
        service = _make_service()
        with patch.object(service, '_ensure_repositories', return_value=[]), \
             patch.object(service, '_discover_repository_at_named_folder',
                          return_value=None):
            with self.assertRaisesRegex(ValueError, 'unknown repository id: nope'):
                service.get_repository('nope')

    def test_returns_direct_lookup_when_inventory_missed_repo(self) -> None:
        # Line 178: ``return direct`` — the inventory walk doesn't
        # include the repo but the direct-folder lookup finds it.
        # This is the fix path for the Windows-operator case where the
        # full inventory walk missed a repo that nevertheless exists
        # at REPOSITORY_ROOT_PATH/<id>/.
        service = _make_service()
        stub_repo = SimpleNamespace(
            id='ob-love-admin-client',
            local_path='/repos/ob-love-admin-client',
        )
        with patch.object(service, '_ensure_repositories', return_value=[]), \
             patch.object(service, '_discover_repository_at_named_folder',
                          return_value=stub_repo) as discover:
            result = service.get_repository('ob-love-admin-client')
        discover.assert_called_once_with('ob-love-admin-client')
        self.assertIs(result, stub_repo)


class WorkspaceHasTaskChangesDefensiveBranchTests(unittest.TestCase):
    """Lines 479-480, 483-484, 495-496 in ``workspace_has_task_changes``:
    OSError on ``.git`` is_dir check, exception in ``_current_branch``,
    exception in ``_ahead_count``."""

    def test_oserror_on_git_dir_check_returns_true(self) -> None:
        # Lines 479-480: ``(Path(local_path) / '.git').is_dir()`` raises
        # OSError (path too long, perms, etc.). Fail-open → return True.
        service = _make_service()
        repo = SimpleNamespace(id='c', local_path='/x')
        with patch.object(Path, 'is_dir', side_effect=OSError('denied')):
            self.assertTrue(
                service.workspace_has_task_changes(repo, 'feat/x'),
            )

    def test_current_branch_exception_returns_true(self) -> None:
        # Lines 483-484: fail-open on ``_current_branch`` failure.
        service = _make_service()
        repo = SimpleNamespace(id='c', local_path='/x')
        with patch.object(Path, 'is_dir', return_value=True), \
             patch.object(service, '_current_branch',
                          side_effect=RuntimeError('git failed')):
            self.assertTrue(
                service.workspace_has_task_changes(repo, 'feat/x'),
            )

    def test_destination_branch_exception_returns_true(self) -> None:
        # Lines 495-496: fail-open on destination_branch / _ahead_count.
        service = _make_service()
        repo = SimpleNamespace(id='c', local_path='/x')
        with patch.object(Path, 'is_dir', return_value=True), \
             patch.object(service, '_current_branch',
                          return_value='feat/x'), \
             patch.object(service, 'destination_branch',
                          side_effect=ValueError('no default')):
            self.assertTrue(
                service.workspace_has_task_changes(repo, 'feat/x'),
            )


if __name__ == '__main__':
    unittest.main()
