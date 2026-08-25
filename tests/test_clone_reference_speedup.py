"""A workspace clone reuses the operator's checkout instead of the network.

The Files-tab sync button re-downloaded a repo's entire history every time
it added one to a task — ~39s for a 144MB repo on the reporter's machine.
The inventory already knows where that repo lives on disk, so the clone can
borrow the objects locally and only pull what's missing (~7s for the same
repo).

The dangerous half is ``--dissociate``. Without it the clone keeps an
``objects/info/alternates`` pointer into the operator's working tree, and
the workspace breaks the day that tree is deleted or gc'd. These tests use
REAL git repositories — a flag assertion alone would not catch a clone that
is fast but secretly dependent.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from kato_core_lib.data_layers.service.repository_service import RepositoryService


def _git(cwd, *args):
    subprocess.run(
        ['git', *args], cwd=str(cwd), check=True,
        capture_output=True, text=True,
    )


class CloneReferenceArgsTests(unittest.TestCase):
    """Which flags get chosen, given what is on disk."""

    def setUp(self) -> None:
        self.service = RepositoryService.__new__(RepositoryService)
        self._tmp = tempfile.TemporaryDirectory(prefix='kato-clone-args-')
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _repo(self, local_path=''):
        return SimpleNamespace(id='r', local_path=str(local_path or ''))

    def test_a_real_checkout_becomes_a_dissociated_reference(self) -> None:
        checkout = self.root / 'checkout'
        (checkout / '.git').mkdir(parents=True)
        args = self.service._clone_speedup_args(
            self._repo(checkout), self.root / 'target',
        )
        self.assertEqual(
            args, ['--reference-if-able', str(checkout), '--dissociate'],
        )

    def test_dissociate_is_never_omitted(self) -> None:
        """The whole safety of the optimisation rests on this flag."""
        checkout = self.root / 'checkout'
        (checkout / '.git').mkdir(parents=True)
        args = self.service._clone_speedup_args(
            self._repo(checkout), self.root / 'target',
        )
        self.assertIn('--dissociate', args)

    def test_no_local_path_means_a_plain_clone(self) -> None:
        self.assertEqual(
            self.service._clone_speedup_args(self._repo(''), self.root / 't'), [],
        )

    def test_a_path_that_is_not_a_repo_is_ignored(self) -> None:
        plain = self.root / 'not-a-repo'
        plain.mkdir()
        self.assertEqual(
            self.service._clone_speedup_args(self._repo(plain), self.root / 't'), [],
        )

    def test_a_missing_path_is_ignored(self) -> None:
        self.assertEqual(
            self.service._clone_speedup_args(
                self._repo(self.root / 'gone'), self.root / 't',
            ),
            [],
        )

    def test_a_repo_is_never_its_own_reference(self) -> None:
        checkout = self.root / 'checkout'
        (checkout / '.git').mkdir(parents=True)
        self.assertEqual(
            self.service._clone_speedup_args(self._repo(checkout), checkout), [],
        )


class CloneReferenceRealGitTests(unittest.TestCase):
    """Real git, real clone: the result must be an ordinary clone."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix='kato-clone-real-')
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

        # An "origin" with a couple of commits, and the operator's checkout.
        self.origin = self.root / 'origin.git'
        source = self.root / 'source'
        source.mkdir()
        _git(source, 'init', '-q', '-b', 'main')
        _git(source, 'config', 'user.email', 't@example.com')
        _git(source, 'config', 'user.name', 'T')
        for n in range(3):
            (source / f'f{n}.txt').write_text(f'content {n}\n')
            _git(source, 'add', '-A')
            _git(source, 'commit', '-qm', f'commit {n}')
        _git(self.root, 'clone', '-q', '--bare', str(source), str(self.origin))

        self.checkout = self.root / 'checkout'
        _git(self.root, 'clone', '-q', str(self.origin), str(self.checkout))

        self.service = RepositoryService.__new__(RepositoryService)
        self.service._validate_git_executable = Mock()
        self.service._run_git = self._run_git_for_real

    def _run_git_for_real(self, cwd, args, _message, _repository=None):
        _git(cwd, *args)

    def _clone(self, local_path):
        target = self.root / 'workspace' / 'repo'
        repository = SimpleNamespace(
            id='repo',
            remote_url=str(self.origin),
            local_path=str(local_path or ''),
        )
        self.service.ensure_clone(repository, target)
        return target

    def test_the_clone_has_the_full_history(self) -> None:
        target = self._clone(self.checkout)
        count = subprocess.run(
            ['git', 'rev-list', '--count', 'HEAD'], cwd=str(target),
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        self.assertEqual(count, '3')

    def test_the_clone_does_not_depend_on_the_reference(self) -> None:
        """No alternates file — the operator can delete their checkout."""
        target = self._clone(self.checkout)
        self.assertFalse((target / '.git' / 'objects' / 'info' / 'alternates').exists())

    def test_the_clone_survives_deleting_the_reference(self) -> None:
        import shutil
        target = self._clone(self.checkout)
        shutil.rmtree(self.checkout)
        # The real proof: every object must still resolve with the borrowed
        # tree gone. A missing --dissociate fails here and nowhere else.
        result = subprocess.run(
            ['git', 'fsck', '--connectivity-only', '--no-progress'],
            cwd=str(target), capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        subprocess.run(
            ['git', 'log', '--oneline'], cwd=str(target),
            capture_output=True, text=True, check=True,
        )

    def test_the_remote_points_at_the_real_origin_not_the_reference(self) -> None:
        target = self._clone(self.checkout)
        url = subprocess.run(
            ['git', 'remote', 'get-url', 'origin'], cwd=str(target),
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        self.assertEqual(url, str(self.origin))

    def test_a_referenced_clone_matches_a_plain_one(self) -> None:
        referenced = self._clone(self.checkout)
        head_ref = subprocess.run(
            ['git', 'rev-parse', 'HEAD'], cwd=str(referenced),
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        import shutil
        shutil.rmtree(referenced)
        plain = self._clone('')
        head_plain = subprocess.run(
            ['git', 'rev-parse', 'HEAD'], cwd=str(plain),
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        self.assertEqual(head_ref, head_plain)

    def test_an_existing_clone_is_left_alone(self) -> None:
        target = self._clone(self.checkout)
        marker = target / 'local-only.txt'
        marker.write_text('do not clobber me\n')
        self._clone(self.checkout)
        self.assertTrue(marker.exists())


if __name__ == '__main__':
    unittest.main()
