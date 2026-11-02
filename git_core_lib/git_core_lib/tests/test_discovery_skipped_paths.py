"""Folders the repository walk is told to stay out of."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from git_core_lib.git_core_lib.helpers.repository_discovery_utils import discover_git_repositories


def make_repository(path: Path) -> None:
    path.mkdir(parents=True)
    subprocess.run(['git', 'init', '-q'], cwd=str(path), check=True)


class SkippedPathsTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve() / 'root'
        make_repository(self.root / 'api')
        make_repository(self.root / 'clones' / 'T-1' / 'api')

    def names(self, root=None, **options) -> list[str]:
        found = discover_git_repositories(str(root or self.root), **options)
        return [str(Path(repository.local_path).relative_to(self.root)) for repository in found]

    def test_everything_is_found_with_nothing_skipped(self) -> None:
        self.assertEqual(
            self.names(), ['api', os.path.join('clones', 'T-1', 'api')],
        )

    def test_a_skipped_folder_is_not_looked_in(self) -> None:
        self.assertEqual(self.names(skipped_paths=[self.root / 'clones']), ['api'])

    def test_a_skipped_path_may_be_text(self) -> None:
        self.assertEqual(self.names(skipped_paths=[str(self.root / 'clones')]), ['api'])

    def test_a_folder_outside_the_root_changes_nothing(self) -> None:
        self.assertEqual(len(self.names(skipped_paths=[self.root.parent / 'elsewhere'])), 2)

    def test_the_root_itself_is_never_skipped(self) -> None:
        self.assertEqual(len(self.names(skipped_paths=[self.root])), 2)

    def test_a_folder_that_only_starts_the_same_is_not_skipped(self) -> None:
        make_repository(self.root / 'clones-old' / 'web')
        self.assertEqual(
            self.names(skipped_paths=[self.root / 'clones']),
            ['api', os.path.join('clones-old', 'web')],
        )

    @unittest.skipUnless(hasattr(os, 'symlink'), 'needs symbolic links')
    def test_a_root_reached_through_a_link_still_skips_the_real_folder(self) -> None:
        link = self.root.parent / 'link'
        link.symlink_to(self.root, target_is_directory=True)
        self.assertEqual(self.names(root=link, skipped_paths=[self.root / 'clones']), ['api'])


if __name__ == '__main__':
    unittest.main()
