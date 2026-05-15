"""Full-coverage unit tests for ``repository_approval_discovery_service``.

The module backs the Settings-drawer repository-approval picker. It
has three discovery sources (inventory config / checkout clones /
per-task workspace clones), a first-source-wins de-dup, and a set of
env-driven path resolvers. Everything here is filesystem + subprocess
boundary code, so the tests stub those boundaries and exercise every
branch (empty/short-circuit, skip-conditions, de-dup, error fallback).
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from kato_core_lib.data_layers.service import (
    repository_approval_discovery_service as mod,
)
from kato_core_lib.data_layers.service.repository_approval_discovery_service import (
    DiscoveredRepository,
    discover_all_repositories,
    discover_checkout_repositories,
    discover_inventory_repositories,
    discover_workspace_repositories,
)


def _git_repo(parent: Path, name: str) -> Path:
    repo = parent / name
    (repo / '.git').mkdir(parents=True)
    return repo


class _FakeDir:
    """Minimal stand-in for a directory entry.

    Needed for the case-insensitive de-dup branch: a real macOS/APFS
    checkout can't have ``Repo`` and ``repo`` as siblings, so the
    ``seen_ids`` collision is unreachable with on-disk fixtures.
    """

    def __init__(self, name: str, *, is_dir: bool = True, has_git: bool = True):
        self.name = name
        self._is_dir = is_dir
        self._has_git = has_git

    def is_dir(self) -> bool:
        return self._is_dir

    def __truediv__(self, _other):  # repo_dir / '.git'
        return SimpleNamespace(exists=lambda: self._has_git)

    def __str__(self) -> str:
        return f'/fake/{self.name}'

    def __lt__(self, other) -> bool:
        return self.name < other.name


class _FakeRoot:
    def __init__(self, children, *, is_dir: bool = True):
        self._children = children
        self._is_dir = is_dir

    def is_dir(self) -> bool:
        return self._is_dir

    def iterdir(self):
        return list(self._children)


class CheckoutDiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_none_root_returns_empty(self) -> None:
        self.assertEqual(discover_checkout_repositories(None), [])

    def test_missing_root_returns_empty(self) -> None:
        self.assertEqual(
            discover_checkout_repositories(self.root / 'nope'), [],
        )

    def test_skips_non_dirs_non_git_and_urlless(self) -> None:
        (self.root / 'a_file').write_text('x', encoding='utf-8')   # not a dir
        (self.root / 'plain_dir').mkdir()                          # no .git
        _git_repo(self.root, 'no_url')                             # .git, no url
        _git_repo(self.root, 'good')
        url_map = {str(self.root / 'good'): 'git@h:org/good.git'}
        with patch.object(
            mod, '_read_origin_url',
            side_effect=lambda p: url_map.get(str(p), ''),
        ):
            out = discover_checkout_repositories(self.root)
        self.assertEqual([r.repository_id for r in out], ['good'])
        self.assertEqual(out[0].source, 'checkout')
        self.assertEqual(out[0].workspace_path, str(self.root / 'good'))

    def test_case_insensitive_dedup_first_wins(self) -> None:
        fake_root = _FakeRoot([_FakeDir('Repo'), _FakeDir('repo')])
        with patch.object(mod, '_read_origin_url', return_value='u'):
            out = discover_checkout_repositories(fake_root)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].repository_id, 'Repo')  # first wins


class WorkspaceDiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_missing_root_returns_empty(self) -> None:
        self.assertEqual(
            discover_workspace_repositories(self.root / 'nope'), [],
        )

    def test_walks_task_then_repo_and_skips_bad_entries(self) -> None:
        (self.root / 'loose_file').write_text('x', encoding='utf-8')  # not a task dir
        task = self.root / 'UNA-9'
        task.mkdir()
        (task / 'a_file').write_text('x', encoding='utf-8')           # repo slot not a dir
        (task / 'no_git').mkdir()                                     # no .git
        _git_repo(task, 'no_url')                                     # .git, no url
        _git_repo(task, 'client')
        url_map = {str(task / 'client'): 'git@h:org/client.git'}
        with patch.object(
            mod, '_read_origin_url',
            side_effect=lambda p: url_map.get(str(p), ''),
        ):
            out = discover_workspace_repositories(self.root)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].repository_id, 'client')
        self.assertEqual(out[0].source, 'workspace')
        self.assertEqual(out[0].task_id, 'UNA-9')

    def test_case_insensitive_dedup_across_tasks(self) -> None:
        for task_name in ('UNA-1', 'UNA-2'):
            t = self.root / task_name
            t.mkdir()
            _git_repo(t, 'client')
        with patch.object(mod, '_read_origin_url', return_value='u'):
            out = discover_workspace_repositories(self.root)
        self.assertEqual(len(out), 1)


class ReadOriginUrlTests(unittest.TestCase):
    def test_subprocess_oserror_returns_empty(self) -> None:
        with patch.object(mod.subprocess, 'run', side_effect=OSError):
            self.assertEqual(mod._read_origin_url(Path('/x')), '')

    def test_subprocess_timeout_returns_empty(self) -> None:
        with patch.object(
            mod.subprocess, 'run',
            side_effect=subprocess.TimeoutExpired(cmd='git', timeout=5),
        ):
            self.assertEqual(mod._read_origin_url(Path('/x')), '')

    def test_nonzero_returncode_returns_empty(self) -> None:
        with patch.object(
            mod.subprocess, 'run',
            return_value=SimpleNamespace(returncode=1, stdout='junk'),
        ):
            self.assertEqual(mod._read_origin_url(Path('/x')), '')

    def test_success_returns_stripped_stdout(self) -> None:
        with patch.object(
            mod.subprocess, 'run',
            return_value=SimpleNamespace(
                returncode=0, stdout='  git@h:org/r.git\n',
            ),
        ):
            self.assertEqual(
                mod._read_origin_url(Path('/x')), 'git@h:org/r.git',
            )


class MergeSourcesTests(unittest.TestCase):
    def test_first_source_wins_and_sorted(self) -> None:
        inv = DiscoveredRepository('Beta', 'inv-url', 'inventory')
        chk = DiscoveredRepository('beta', 'chk-url', 'checkout')
        alpha = DiscoveredRepository('alpha', 'a-url', 'checkout')
        merged = mod._merge_sources([inv], [chk, alpha])
        self.assertEqual([r.repository_id for r in merged], ['alpha', 'Beta'])
        beta = next(r for r in merged if r.repository_id.lower() == 'beta')
        self.assertEqual(beta.source, 'inventory')  # inventory won


class PathResolverTests(unittest.TestCase):
    def test_workspaces_root_env_override(self) -> None:
        with patch.dict(os.environ, {'KATO_WORKSPACES_ROOT': '/ws/here'}):
            self.assertEqual(
                mod._resolve_workspaces_root(), Path('/ws/here'),
            )

    def test_workspaces_root_default(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('KATO_WORKSPACES_ROOT', None)
            with patch.object(
                mod.Path, 'home', return_value=Path('/home/op'),
            ):
                self.assertEqual(
                    mod._resolve_workspaces_root(),
                    Path('/home/op/.kato/workspaces'),
                )

    def test_repository_root_unset_is_none(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('REPOSITORY_ROOT_PATH', None)
            self.assertIsNone(mod._resolve_repository_root())

    def test_repository_root_env_override(self) -> None:
        with patch.dict(os.environ, {'REPOSITORY_ROOT_PATH': '/src'}):
            self.assertEqual(mod._resolve_repository_root(), Path('/src'))


class KatoConfigPathTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def test_env_pointing_at_file(self) -> None:
        cfg = self.dir / 'kato.yaml'
        cfg.write_text('kato: {}', encoding='utf-8')
        with patch.dict(os.environ, {'KATO_CONFIG': str(cfg)}):
            self.assertEqual(mod._kato_config_path(), cfg)

    def test_env_pointing_at_missing_file_is_none(self) -> None:
        with patch.dict(os.environ, {'KATO_CONFIG': str(self.dir / 'gone.yaml')}):
            self.assertIsNone(mod._kato_config_path())

    def test_candidate_discovery_and_no_candidate(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('KATO_CONFIG', None)
            with patch.object(mod.Path, 'cwd', return_value=self.dir), \
                 patch.object(mod.Path, 'home', return_value=self.dir):
                self.assertIsNone(mod._kato_config_path())  # none exist
                (self.dir / 'kato.yaml').write_text('x', encoding='utf-8')
                self.assertEqual(
                    mod._kato_config_path(), self.dir / 'kato.yaml',
                )


class InventoryDiscoveryTests(unittest.TestCase):
    def test_import_failure_returns_empty(self) -> None:
        # omegaconf unavailable → the guarded import raises → [].
        with patch.dict(sys.modules, {'omegaconf': None}):
            self.assertEqual(discover_inventory_repositories(), [])

    def test_missing_config_path_returns_empty(self) -> None:
        with patch.object(mod, '_kato_config_path', return_value=None):
            self.assertEqual(discover_inventory_repositories(), [])

    def test_omegaconf_load_exception_returns_empty(self) -> None:
        cfg = Path(tempfile.mkdtemp()) / 'kato.yaml'
        cfg.write_text('kato: {}', encoding='utf-8')
        self.addCleanup(lambda: cfg.unlink(missing_ok=True))
        with patch.object(mod, '_kato_config_path', return_value=cfg), \
             patch('omegaconf.OmegaConf.load', side_effect=RuntimeError):
            self.assertEqual(discover_inventory_repositories(), [])

    def test_happy_path_filters_dedups_and_sorts(self) -> None:
        tmp = tempfile.mkdtemp()
        cfg = Path(tmp) / 'kato.yaml'
        cfg.write_text('kato:\n  repositories: []\n', encoding='utf-8')
        self.addCleanup(lambda: cfg.unlink(missing_ok=True))

        repos = [
            SimpleNamespace(id='Zeta', remote_url='z-url'),
            SimpleNamespace(id='', remote_url='skip-no-id'),
            SimpleNamespace(id='NoUrl', remote_url=''),
            SimpleNamespace(id='alpha', remote_url='a-url'),
            SimpleNamespace(id='ALPHA', remote_url='dupe'),  # case dup
        ]
        fake_service = MagicMock()
        fake_service.repositories = repos
        with patch.object(mod, '_kato_config_path', return_value=cfg), \
             patch(
                 'kato_core_lib.data_layers.service.'
                 'repository_inventory_service.RepositoryInventoryService',
                 return_value=fake_service,
             ):
            out = discover_inventory_repositories()
        self.assertEqual([r.repository_id for r in out], ['alpha', 'Zeta'])
        self.assertTrue(all(r.source == 'inventory' for r in out))


class DiscoverAllTests(unittest.TestCase):
    def test_aggregates_three_sources_inventory_wins(self) -> None:
        with patch.object(
            mod, 'discover_inventory_repositories',
            return_value=[DiscoveredRepository('repo', 'inv', 'inventory')],
        ), patch.object(
            mod, 'discover_checkout_repositories',
            return_value=[DiscoveredRepository('repo', 'chk', 'checkout')],
        ), patch.object(
            mod, 'discover_workspace_repositories',
            return_value=[DiscoveredRepository('other', 'ws', 'workspace')],
        ), patch.object(
            mod, '_resolve_repository_root', return_value=None,
        ), patch.object(
            mod, '_resolve_workspaces_root', return_value=Path('/ws'),
        ):
            out = discover_all_repositories()
        by_id = {r.repository_id: r for r in out}
        self.assertEqual(by_id['repo'].source, 'inventory')
        self.assertEqual(
            [r.repository_id for r in out], ['other', 'repo'],
        )


if __name__ == '__main__':
    unittest.main()
