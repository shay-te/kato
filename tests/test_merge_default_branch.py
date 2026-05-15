"""``Merge master`` — fetch + merge the default branch into a task
branch so the (git-blocked) agent can resolve conflicts by editing
files.

Two layers:
  * RepositoryService.merge_default_branch_into_clone — preflight
    refusals (mocked) + a real on-disk git repo for the clean-merge
    and conflict paths (the conflict path is the whole point: markers
    must be LEFT in the tree, not aborted).
  * agent_service.merge_default_branch_for_task — aggregation across
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

    def test_dirty_working_tree_refused(self) -> None:
        repo = SimpleNamespace(id='c', local_path='/x')
        with patch.object(Path, 'is_dir', return_value=True), \
             patch.object(self.service, '_current_branch',
                          return_value='feat/x'), \
             patch.object(self.service, '_working_tree_status',
                          return_value=' M file.py'):
            out = self.service.merge_default_branch_into_clone(repo, 'feat/x')
        self.assertEqual(out['reason'], 'dirty_working_tree')


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
        from kato_core_lib.data_layers.service.agent_service import AgentService
        svc = AgentService.__new__(AgentService)
        svc.logger = MagicMock()
        svc._repository_service = MagicMock()
        svc._repository_service.build_branch_name.return_value = 'feat/x'
        return svc

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


if __name__ == '__main__':
    unittest.main()
