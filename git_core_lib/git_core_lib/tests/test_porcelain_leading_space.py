"""Porcelain status must keep its leading whitespace.

``git status --porcelain`` puts the index status in column 1 and the
worktree status in column 2, so an unstaged-only change reads ``" M path"``
with a SIGNIFICANT leading space. Consumers slice ``line[3:]`` to recover the
path.

``_working_tree_status`` used to route through ``_git_stdout``, which strips
both ends. That shifted the FIRST line one column left, so its path lost a
character — and only its first line, which is why it survived review.

Not cosmetic: the generated-artifact classifier matches on the path's
top-level directory name, so ``" M xbuild/f"`` arrived as ``"build/f"`` and a
file under ``xbuild/`` was classified as disposable build output.
"""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from git_core_lib.git_core_lib.client.git_client import GitClientMixin
from git_core_lib.git_core_lib.helpers.git_clean_utils import status_paths


def _git(cwd, *args):
    subprocess.run(['git', *args], cwd=str(cwd), check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class _Client(GitClientMixin):
    pass


class PorcelainLeadingSpaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name) / 'r'
        self.repo.mkdir()
        _git(self.repo, 'init', '-q', '-b', 'main')
        _git(self.repo, 'config', 'user.email', 't@example.com')
        _git(self.repo, 'config', 'user.name', 'test')
        (self.repo / 'xbuild').mkdir()
        (self.repo / 'xbuild' / 'source.py').write_text('x = 1\n', encoding='utf-8')
        (self.repo / 'other.py').write_text('y = 2\n', encoding='utf-8')
        _git(self.repo, 'add', '-A')
        _git(self.repo, 'commit', '-qm', 'initial')
        self.client = _Client()

    def _status(self):
        return self.client._working_tree_status(str(self.repo))

    def test_the_first_line_keeps_its_leading_space(self) -> None:
        (self.repo / 'xbuild' / 'source.py').write_text('EDITED\n', encoding='utf-8')
        first = self._status().splitlines()[0]
        self.assertTrue(
            first.startswith(' M '),
            f'leading status column was stripped: {first!r}',
        )

    def test_the_first_path_is_not_truncated(self) -> None:
        # THE BUG. 'xbuild/source.py' arrived as 'build/source.py'.
        (self.repo / 'xbuild' / 'source.py').write_text('EDITED\n', encoding='utf-8')
        self.assertEqual(status_paths(self._status()), ['xbuild/source.py'])

    def test_a_path_under_xbuild_is_not_mistaken_for_build_output(self) -> None:
        # The consequence: the artifact classifier keys on the top-level
        # directory name, so the truncated path looked disposable.
        (self.repo / 'xbuild' / 'source.py').write_text('EDITED\n', encoding='utf-8')
        for path in status_paths(self._status()):
            self.assertNotEqual(
                path.split('/', 1)[0], 'build',
                f'{path!r} would be treated as generated build output',
            )

    def test_every_line_survives_not_just_the_later_ones(self) -> None:
        # Only the FIRST line was affected, which is how it went unnoticed.
        (self.repo / 'xbuild' / 'source.py').write_text('EDITED\n', encoding='utf-8')
        (self.repo / 'other.py').write_text('EDITED\n', encoding='utf-8')
        self.assertEqual(
            sorted(status_paths(self._status())),
            ['other.py', 'xbuild/source.py'],
        )

    def test_a_clean_tree_is_still_empty(self) -> None:
        # rstrip('\n') must not turn "no changes" into something truthy.
        self.assertEqual(self._status(), '')
        self.assertEqual(status_paths(self._status()), [])


if __name__ == '__main__':
    unittest.main()
