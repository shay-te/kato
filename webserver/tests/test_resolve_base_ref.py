"""Real-git tests for the diff-base ref resolver.

``resolve_base_ref`` / ``has_origin_remote`` decide what the Changes tab
and Files tree diff against. The behaviour is inherently about on-disk git
state (does an ``origin`` remote exist? does ``origin/<base>`` resolve?), so
these use a real repo rather than stubbing ``run_git``.

The bug they pin: a git folder the operator copied straight into a task —
a repo with NO ``origin`` remote — used to show "Nothing changed yet"
because the diff base was ``origin/<base>`` (a ref that doesn't exist).
Now it falls back to ``HEAD`` so the working-tree edits show.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from kato_webserver import git_diff_utils
from kato_webserver.git_diff_utils import (
    changed_paths,
    has_origin_remote,
    resolve_base_ref,
)


def _git(cwd, *args):
    subprocess.run(['git', *args], cwd=str(cwd), check=True,
                   capture_output=True, text=True)


def _init_repo(path: Path):
    path.mkdir(parents=True, exist_ok=True)
    _git(path, 'init', '-q')
    _git(path, 'config', 'user.email', 't@example.com')
    _git(path, 'config', 'user.name', 'Test')
    _git(path, 'checkout', '-q', '-b', 'main')
    (path / 'file.py').write_text('original\n', encoding='utf-8')
    _git(path, 'add', '-A')
    _git(path, 'commit', '-q', '-m', 'base')


class NoRemoteCloneTests(unittest.TestCase):
    """A copy-pasted local repo (no origin) must still show its changes."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name) / 'clone'
        _init_repo(self.repo)

    def test_has_origin_remote_false_without_a_remote(self) -> None:
        self.assertFalse(has_origin_remote(str(self.repo)))

    def test_resolve_base_ref_falls_back_to_head_when_base_unknown(self) -> None:
        # The real flow: with no origin, ``detect_default_branch`` returns ''
        # so the resolver is handed an empty base → HEAD fallback.
        ref, is_local = resolve_base_ref(str(self.repo), '')
        self.assertEqual(ref, 'HEAD')
        self.assertTrue(is_local)

    def test_a_local_base_branch_is_used_when_named(self) -> None:
        # If a base name IS known and exists locally (no remote), diff against
        # that local branch rather than HEAD.
        ref, is_local = resolve_base_ref(str(self.repo), 'main')
        self.assertEqual(ref, 'main')
        self.assertFalse(is_local)

    def test_working_tree_edit_shows_via_head_fallback(self) -> None:
        # The operator (or Claude) edits a tracked file — uncommitted, exactly
        # what they see in VSCode. origin/main doesn't exist and no base is
        # known, so the old code returned []. The HEAD fallback surfaces it.
        (self.repo / 'file.py').write_text('CHANGED BY CLAUDE\n', encoding='utf-8')
        ref, _ = resolve_base_ref(str(self.repo), '')
        self.assertEqual(ref, 'HEAD')
        self.assertIn('file.py', changed_paths(str(self.repo), ref))

    def test_new_untracked_file_shows_too(self) -> None:
        (self.repo / 'fresh.py').write_text('new\n', encoding='utf-8')
        ref, _ = resolve_base_ref(str(self.repo), '')
        self.assertIn('fresh.py', changed_paths(str(self.repo), ref))


class WithOriginRemoteTests(unittest.TestCase):
    """A normal cloud clone keeps using origin/<base>."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        origin = tmp / 'origin.git'
        seed = tmp / 'seed'
        _init_repo(seed)
        _git(seed, 'clone', '-q', '--bare', str(seed), str(origin))
        self.clone = tmp / 'clone'
        _git(tmp, 'clone', '-q', str(origin), str(self.clone))
        _git(self.clone, 'config', 'user.email', 't@example.com')
        _git(self.clone, 'config', 'user.name', 'Test')

    def test_has_origin_remote_true(self) -> None:
        self.assertTrue(has_origin_remote(str(self.clone)))

    def test_resolves_to_origin_base(self) -> None:
        ref, is_local = resolve_base_ref(str(self.clone), 'main')
        self.assertEqual(ref, 'origin/main')
        self.assertFalse(is_local)

    def test_unknown_base_with_remote_is_local_fallback(self) -> None:
        # A remote exists but the configured base is bogus (no origin/nope,
        # no local nope) → flagged local so the caller surfaces the config
        # error instead of silently HEAD-diffing.
        ref, is_local = resolve_base_ref(str(self.clone), 'nope')
        self.assertEqual(ref, 'HEAD')
        self.assertTrue(is_local)


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
