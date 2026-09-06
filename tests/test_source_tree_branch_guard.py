"""kato must never create a task branch in the operator's SOURCE checkout.

In workspace mode each task gets its own clone under the workspaces root and
branch prep runs against THAT. The repository objects carrying workspace
paths are shallow copies: ``provision_task_workspace_clones`` returns the
inventory ORIGINALS untouched whenever the workspace service is missing, so
one unwired dependency points branch prep at the operator's live source tree
instead.

It does not crash when that happens. Branches quietly appear in the folders
the operator works in every day, while the task clone the agent is actually
editing stays on master and never produces a PR — reported as "he creates the
branches inside my dev-una folder repos and keeps the task repos on master
that claude works on".

Two roots are configured and distinct in workspace mode, so a path under the
source root is a wiring bug by definition. The guard is fail-closed and runs
BEFORE any git: one failed task costs far less than branches and checkouts in
repositories kato does not own — that source tree is a running system.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from kato_core_lib.data_layers.service.repository_service import RepositoryService


class _Service(RepositoryService):
    """Records which repos actually reached git."""

    def __init__(self):
        self.prepared: list[str] = []
        self.logger = SimpleNamespace(
            info=lambda *a, **k: None, warning=lambda *a, **k: None,
            exception=lambda *a, **k: None, error=lambda *a, **k: None,
        )

    def _validate_git_executable(self):
        return None

    def _prepare_task_branch(self, repository, branch_name):
        self.prepared.append(str(getattr(repository, 'local_path', '')))
        return repository


class SourceTreeBranchGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.source = root / 'dev-una'
        self.workspaces = root / 'dev-kato'
        (self.source / 'form-core-lib').mkdir(parents=True)
        (self.workspaces / 'UNA-3025' / 'form-core-lib').mkdir(parents=True)
        self.service = _Service()

    def _env(self, **overrides):
        env = {
            'REPOSITORY_ROOT_PATH': str(self.source),
            'KATO_WORKSPACES_ROOT': str(self.workspaces),
        }
        env.update(overrides)
        return mock.patch.dict(os.environ, env)

    def _prepare(self, local_path):
        repository = SimpleNamespace(id='form-core-lib', local_path=str(local_path))
        return self.service.prepare_task_branches(
            [repository], {'form-core-lib': 'UNA-3025'},
        )

    def test_a_source_tree_path_is_REFUSED(self) -> None:
        # THE REPORT. Nothing reaches git.
        with self._env(), self.assertRaises(RuntimeError) as caught:
            self._prepare(self.source / 'form-core-lib')
        self.assertEqual(self.service.prepared, [])
        message = str(caught.exception)
        self.assertIn('source tree', message)
        self.assertIn('UNA-3025', message)

    def test_the_refusal_names_both_roots_so_it_is_actionable(self) -> None:
        # "kato is buggy" is not a diagnosis. The message has to say which
        # path it got, which tree that is, and where it expected to be.
        with self._env(), self.assertRaises(RuntimeError) as caught:
            self._prepare(self.source / 'form-core-lib')
        message = str(caught.exception)
        self.assertIn(str(self.source), message)
        self.assertIn(str(self.workspaces), message)
        self.assertIn('KATO_WORKSPACES_ROOT', message)

    def test_the_workspace_clone_is_allowed(self) -> None:
        # The whole point — the normal path must be untouched.
        target = self.workspaces / 'UNA-3025' / 'form-core-lib'
        with self._env():
            self._prepare(target)
        self.assertEqual(self.service.prepared, [str(target)])

    def test_a_legacy_single_clone_install_still_works(self) -> None:
        # A legacy install has NO per-task clones, so no workspaces root on
        # disk — and its own checkout is the thing to branch. Refusing would
        # break it outright.
        #
        # Pointed at a path that does not exist rather than left EMPTY: an
        # empty variable no longer means "legacy". The documented default is
        # "Empty = ~/.kato/workspaces", so reading the raw variable made the
        # guard inert on every ordinary install — the protection was believed
        # to be on and was not. The guard now resolves that default and keys
        # on whether the root EXISTS, which is what actually separates the
        # two installs.
        missing_root = str(self.source.parent / 'no-such-workspaces-root')
        with mock.patch.dict(
            os.environ,
            {'REPOSITORY_ROOT_PATH': str(self.source),
             'KATO_WORKSPACES_ROOT': missing_root},
        ):
            self._prepare(self.source / 'form-core-lib')
        self.assertEqual(len(self.service.prepared), 1)

    def test_roots_pointing_at_the_same_tree_are_not_refused(self) -> None:
        # A deliberate same-tree configuration is not the bug this catches.
        with self._env(KATO_WORKSPACES_ROOT=str(self.source)):
            self._prepare(self.source / 'form-core-lib')
        self.assertEqual(len(self.service.prepared), 1)

    def test_a_path_outside_both_roots_is_left_alone(self) -> None:
        # Explicitly-configured repos can live anywhere; the guard only
        # claims authority over the source tree it was told about.
        outside = Path(self._tmp.name) / 'elsewhere' / 'repo'
        outside.mkdir(parents=True)
        with self._env():
            self._prepare(outside)
        self.assertEqual(self.service.prepared, [str(outside)])

    def test_a_workspace_nested_inside_the_source_root_is_allowed(self) -> None:
        # Some installs put the workspaces root under the source root. The
        # workspace check must win, or that layout can never branch at all.
        nested = self.source / 'workspaces'
        clone = nested / 'UNA-3025' / 'form-core-lib'
        clone.mkdir(parents=True)
        with self._env(KATO_WORKSPACES_ROOT=str(nested)):
            self._prepare(clone)
        self.assertEqual(self.service.prepared, [str(clone)])

    def test_an_unresolvable_path_does_not_break_the_task(self) -> None:
        # The guard is a safety net, not a new failure mode: if it cannot
        # resolve the paths it steps aside rather than blocking work.
        target = self.workspaces / 'UNA-3025' / 'form-core-lib'
        with self._env(), mock.patch.object(
            Path, 'resolve', side_effect=OSError('unreadable'),
        ):
            self._prepare(target)
        self.assertEqual(self.service.prepared, [str(target)])


if __name__ == '__main__':
    unittest.main()
