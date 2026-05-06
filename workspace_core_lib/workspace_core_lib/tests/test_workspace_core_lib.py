"""End-to-end smoke tests for the :class:`WorkspaceCoreLib` entry point.

The component-level tests in this folder cover the layers in
isolation; this file pins the integration story — that constructing
``WorkspaceCoreLib`` wires up a working ``workspaces`` service and
``orphan_scanner`` against the same root.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from workspace_core_lib.workspace_core_lib import (
    WORKSPACE_STATUS_ACTIVE,
    OrphanWorkspaceScannerService,
    WorkspaceCoreLib,
    WorkspaceService,
)


class WorkspaceCoreLibTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.lib = WorkspaceCoreLib(root=self.root)

    def test_exposes_workspaces_service(self) -> None:
        self.assertIsInstance(self.lib.workspaces, WorkspaceService)

    def test_exposes_orphan_scanner(self) -> None:
        self.assertIsInstance(
            self.lib.orphan_scanner, OrphanWorkspaceScannerService,
        )

    def test_workspaces_and_orphan_scanner_share_a_root(self) -> None:
        # Smoke: a record created via the service is visible to the
        # scanner as a non-orphan (it has metadata).
        self.lib.workspaces.create(task_id='PROJ-1', task_summary='hi')
        self.assertEqual(self.lib.orphan_scanner.scan(), [])
        # And a folder dropped under the same root WITHOUT metadata
        # is detected as orphan.
        (self.root / 'ORPHAN').mkdir()
        names = [o.task_id for o in self.lib.orphan_scanner.scan()]
        self.assertEqual(names, ['ORPHAN'])

    def test_max_parallel_tasks_propagates(self) -> None:
        lib = WorkspaceCoreLib(root=self.root, max_parallel_tasks=8)
        self.assertEqual(lib.workspaces.max_parallel_tasks, 8)

    def test_metadata_filename_propagates(self) -> None:
        lib = WorkspaceCoreLib(
            root=self.root,
            metadata_filename='.custom-meta.json',
        )
        record = lib.workspaces.create(task_id='PROJ-1')
        # The custom filename ended up under the workspace folder.
        self.assertTrue(
            (self.root / 'PROJ-1' / '.custom-meta.json').is_file(),
        )

    def test_preflight_log_filename_propagates(self) -> None:
        lib = WorkspaceCoreLib(
            root=self.root,
            preflight_log_filename='.custom-progress.log',
        )
        lib.workspaces.create(task_id='PROJ-1')
        path = lib.workspaces.preflight_log_path('PROJ-1')
        self.assertEqual(path.name, '.custom-progress.log')

    def test_full_lifecycle_create_update_delete(self) -> None:
        # The high-touch end-to-end path: create → mark active →
        # bind agent session → list → delete.
        record = self.lib.workspaces.create(
            task_id='LIFE-1',
            task_summary='lifecycle',
            repository_ids=['client', 'backend'],
        )
        self.assertEqual(record.task_id, 'LIFE-1')

        self.lib.workspaces.update_status('LIFE-1', WORKSPACE_STATUS_ACTIVE)
        self.lib.workspaces.update_agent_session(
            'LIFE-1',
            agent_session_id='sess-uuid',
            cwd=str(self.root / 'LIFE-1' / 'client'),
        )

        listed = self.lib.workspaces.list_workspaces()
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0].status, WORKSPACE_STATUS_ACTIVE)
        self.assertEqual(listed[0].agent_session_id, 'sess-uuid')

        self.lib.workspaces.delete('LIFE-1')
        self.assertEqual(self.lib.workspaces.list_workspaces(), [])


if __name__ == '__main__':
    unittest.main()
