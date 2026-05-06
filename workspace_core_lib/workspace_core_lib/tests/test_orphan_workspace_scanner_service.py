"""Filesystem-scanning tests for :class:`OrphanWorkspaceScannerService`."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from workspace_core_lib.workspace_core_lib.data_layers.data_access.workspace_data_access import (
    DEFAULT_METADATA_FILENAME,
    WorkspaceDataAccess,
)
from workspace_core_lib.workspace_core_lib.data_layers.service.orphan_workspace_scanner_service import (
    OrphanWorkspaceScannerService,
)


class OrphanWorkspaceScannerServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.data_access = WorkspaceDataAccess(root=self.root)
        self.scanner = OrphanWorkspaceScannerService(self.data_access)

    def test_constructor_rejects_missing_data_access(self) -> None:
        with self.assertRaisesRegex(ValueError, 'data_access is required'):
            OrphanWorkspaceScannerService(None)  # type: ignore[arg-type]

    def test_scan_returns_empty_when_root_missing(self) -> None:
        import shutil
        shutil.rmtree(self.root)
        self.assertEqual(self.scanner.scan(), [])

    def test_scan_returns_empty_when_no_orphans(self) -> None:
        # Workspace has metadata → not an orphan.
        (self.root / 'OK').mkdir()
        (self.root / 'OK' / DEFAULT_METADATA_FILENAME).write_text(
            '{}', encoding='utf-8',
        )
        self.assertEqual(self.scanner.scan(), [])

    def test_scan_finds_folder_without_metadata(self) -> None:
        (self.root / 'ORPHAN-1').mkdir()
        orphans = self.scanner.scan()
        self.assertEqual(len(orphans), 1)
        self.assertEqual(orphans[0].task_id, 'ORPHAN-1')
        self.assertEqual(orphans[0].path, self.root / 'ORPHAN-1')
        self.assertEqual(orphans[0].git_repository_dirs, ())

    def test_scan_detects_git_subdirs_inside_orphan(self) -> None:
        (self.root / 'ORPHAN-1').mkdir()
        (self.root / 'ORPHAN-1' / 'client' / '.git').mkdir(parents=True)
        (self.root / 'ORPHAN-1' / 'backend' / '.git').mkdir(parents=True)
        (self.root / 'ORPHAN-1' / 'docs').mkdir()  # no .git → ignored
        orphans = self.scanner.scan()
        self.assertEqual(len(orphans), 1)
        self.assertEqual(
            orphans[0].git_repository_dirs, ('backend', 'client'),
        )

    def test_scan_ignores_files_at_root(self) -> None:
        (self.root / 'stray.txt').write_text('ignore', encoding='utf-8')
        self.assertEqual(self.scanner.scan(), [])

    def test_scan_returns_sorted_results(self) -> None:
        for name in ('Z-orphan', 'A-orphan', 'M-orphan'):
            (self.root / name).mkdir()
        names = [o.task_id for o in self.scanner.scan()]
        self.assertEqual(names, ['A-orphan', 'M-orphan', 'Z-orphan'])

    def test_scan_mixes_orphans_and_registered_workspaces(self) -> None:
        (self.root / 'REGISTERED').mkdir()
        (self.root / 'REGISTERED' / DEFAULT_METADATA_FILENAME).write_text(
            '{}', encoding='utf-8',
        )
        (self.root / 'ORPHAN').mkdir()
        names = [o.task_id for o in self.scanner.scan()]
        self.assertEqual(names, ['ORPHAN'])

    def test_scan_treats_empty_folder_as_orphan_with_no_repos(self) -> None:
        # An empty folder is still surfaced as an orphan so the host
        # can decide what to do (skip / discard / ...).
        (self.root / 'EMPTY').mkdir()
        orphans = self.scanner.scan()
        self.assertEqual(len(orphans), 1)
        self.assertEqual(orphans[0].git_repository_dirs, ())

    def test_scan_honors_custom_metadata_filename(self) -> None:
        # When a host pins a different metadata filename (kato uses
        # ``.kato-meta.json``), the scanner must check for THAT
        # specific name — not the default.
        custom_da = WorkspaceDataAccess(
            root=self.root, metadata_filename='.kato-meta.json',
        )
        custom_scanner = OrphanWorkspaceScannerService(custom_da)
        # Folder with the lib's default name is still an orphan from
        # the kato-pinned scanner's POV.
        (self.root / 'LOOKS-MANAGED').mkdir()
        (self.root / 'LOOKS-MANAGED' / DEFAULT_METADATA_FILENAME).write_text(
            '{}', encoding='utf-8',
        )
        names = [o.task_id for o in custom_scanner.scan()]
        self.assertIn('LOOKS-MANAGED', names)


if __name__ == '__main__':
    unittest.main()
