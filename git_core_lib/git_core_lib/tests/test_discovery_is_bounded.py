"""Discovery must not walk the whole world to find repositories.

The walk descends into a repository it has already found, because operators
do nest independent checkouts inside a parent one. It was descending the
ENTIRE tree though: on a normal projects folder 98% of the directories it
visited were inside a repo it had already recorded (33,188 of 33,868 on the
reporter's machine), which is why "scan for new repositories" took seconds.

Nested repositories sit a level or two down. Nothing legitimate is twenty
levels inside another repo's source tree — the one case found that deep in
practice was a package's own bundled checkout inside a virtualenv.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from git_core_lib.git_core_lib.helpers.repository_discovery_utils import (
    DISCOVERY_SKIP_DIRS,
    MAX_DEPTH_INSIDE_REPOSITORY,
    discover_git_repositories,
)


def _repo(path: Path) -> None:
    (path / '.git').mkdir(parents=True, exist_ok=True)
    (path / '.git' / 'config').write_text(
        '[remote "origin"]\n\turl = ssh://git@h/x.git\n', encoding='utf-8',
    )


class BoundedDiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix='discovery-bound-')
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _names(self):
        return sorted(
            Path(r.local_path).relative_to(self.root.resolve()).as_posix()
            for r in discover_git_repositories(str(self.root))
        )

    def test_top_level_repositories_are_found(self) -> None:
        _repo(self.root / 'alpha')
        _repo(self.root / 'beta')
        self.assertEqual(self._names(), ['alpha', 'beta'])

    def test_a_repo_nested_just_inside_another_is_still_found(self) -> None:
        # The case the descent exists for.
        _repo(self.root / 'parent')
        _repo(self.root / 'parent' / 'child')
        self.assertEqual(self._names(), ['parent', 'parent/child'])

    def test_a_repo_at_the_depth_LIMIT_is_still_found(self) -> None:
        parent = self.root / 'parent'
        _repo(parent)
        deep = parent.joinpath(*['d'] * MAX_DEPTH_INSIDE_REPOSITORY)
        _repo(deep)
        self.assertIn(
            f'parent/{"/".join(["d"] * MAX_DEPTH_INSIDE_REPOSITORY)}',
            self._names(),
        )

    def test_the_walk_STOPS_below_the_limit(self) -> None:
        """The property that makes the scan fast."""
        parent = self.root / 'parent'
        _repo(parent)
        too_deep = parent.joinpath(*['d'] * (MAX_DEPTH_INSIDE_REPOSITORY + 2))
        _repo(too_deep)
        self.assertEqual(self._names(), ['parent'])

    def test_the_limit_is_measured_from_the_NEAREST_repo(self) -> None:
        # A nested repo resets the budget, so a chain of nested checkouts is
        # still discovered however long it is.
        _repo(self.root / 'a')
        _repo(self.root / 'a' / 'b')
        _repo(self.root / 'a' / 'b' / 'c')
        self.assertEqual(self._names(), ['a', 'a/b', 'a/b/c'])

    def test_depth_does_not_leak_between_sibling_branches(self) -> None:
        # A deep branch under one repo must not stop a shallow sibling being
        # walked — the bound is per-branch, not a global counter.
        _repo(self.root / 'repo')
        deep = self.root / 'repo' / 'x' / 'y' / 'z' / 'w'
        deep.mkdir(parents=True)
        _repo(self.root / 'repo' / 'sibling')
        self.assertIn('repo/sibling', self._names())

    def test_directories_OUTSIDE_any_repo_are_walked_freely(self) -> None:
        # The bound applies only inside a repository; a deep plain folder
        # structure must still be searched.
        deep = self.root.joinpath(*['plain'] * 8)
        _repo(deep)
        self.assertEqual(self._names(), ['/'.join(['plain'] * 8)])


class VendorTreesAreSkippedTests(unittest.TestCase):
    """Third-party checkouts are not the operator's repositories."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix='discovery-skip-')
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_a_bundled_checkout_inside_a_venv_is_ignored(self) -> None:
        # Exactly what was being offered in the repo picker: a package's own
        # ``.git`` under site-packages.
        _repo(self.root / 'project')
        _repo(
            self.root / 'project' / 'venv' / 'lib' / 'site-packages' / 'dep',
        )
        found = [
            Path(r.local_path).name
            for r in discover_git_repositories(str(self.root))
        ]
        self.assertEqual(found, ['project'])

    def test_the_skip_list_covers_the_usual_dependency_trees(self) -> None:
        for name in ('venv', 'site-packages', 'node_modules', '.venv', '.tox'):
            self.assertIn(name, DISCOVERY_SKIP_DIRS)


if __name__ == '__main__':
    unittest.main()
