"""Unit tests for kato.data_layers.service.workspace_manager."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from kato_core_lib.data_layers.service.workspace_manager import (
    WORKSPACE_STATUS_ACTIVE,
    WORKSPACE_STATUS_DONE,
    WORKSPACE_STATUS_ERRORED,
    WORKSPACE_STATUS_PROVISIONING,
    WorkspaceManager,
    WorkspaceRecord,
)


class WorkspaceManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.manager = WorkspaceManager(root=self.root, max_parallel_tasks=4)

    def test_create_makes_folder_and_metadata_file(self) -> None:
        record = self.manager.create(
            task_id='PROJ-1',
            task_summary='something',
            repository_ids=['client'],
        )
        self.assertEqual(record.task_id, 'PROJ-1')
        self.assertEqual(record.repository_ids, ['client'])
        self.assertEqual(record.status, WORKSPACE_STATUS_PROVISIONING)
        workspace = self.root / 'PROJ-1'
        self.assertTrue(workspace.is_dir())
        meta = json.loads((workspace / '.kato-meta.json').read_text())
        self.assertEqual(meta['task_id'], 'PROJ-1')
        self.assertEqual(meta['task_summary'], 'something')
        self.assertEqual(meta['repository_ids'], ['client'])
        self.assertTrue(meta['resume_on_startup'])

    def test_create_is_idempotent_and_preserves_created_at(self) -> None:
        first = self.manager.create(task_id='PROJ-1', task_summary='one')
        # Second create with a fresh summary keeps the original creation
        # timestamp but updates the summary + bumps updated_at.
        second = self.manager.create(task_id='PROJ-1', task_summary='two')
        self.assertEqual(second.task_id, 'PROJ-1')
        self.assertEqual(second.task_summary, 'two')
        self.assertEqual(second.created_at_epoch, first.created_at_epoch)
        self.assertGreaterEqual(second.updated_at_epoch, first.updated_at_epoch)

    def test_repository_path_is_under_workspace(self) -> None:
        path = self.manager.repository_path('PROJ-1', 'client')
        self.assertEqual(path, self.root / 'PROJ-1' / 'client')

    def test_get_returns_persisted_record(self) -> None:
        self.manager.create(
            task_id='PROJ-2',
            task_summary='foo',
            repository_ids=['a', 'b'],
        )
        record = self.manager.get('PROJ-2')
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.task_summary, 'foo')
        self.assertEqual(record.repository_ids, ['a', 'b'])

    def test_get_returns_errored_record_when_metadata_missing(self) -> None:
        # A folder exists but no .kato-meta.json — half-initialized
        # workspace from a kato crash.
        (self.root / 'PROJ-3').mkdir()
        record = self.manager.get('PROJ-3')
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.status, WORKSPACE_STATUS_ERRORED)

    def test_get_returns_none_for_missing_workspace(self) -> None:
        self.assertIsNone(self.manager.get('NOPE-1'))

    def test_list_workspaces_returns_every_subfolder(self) -> None:
        self.manager.create(task_id='PROJ-1', task_summary='one')
        self.manager.create(task_id='PROJ-2', task_summary='two')
        # And a half-broken one
        (self.root / 'PROJ-3').mkdir()
        records = self.manager.list_workspaces()
        ids = [r.task_id for r in records]
        self.assertIn('PROJ-1', ids)
        self.assertIn('PROJ-2', ids)
        self.assertIn('PROJ-3', ids)
        broken = next(r for r in records if r.task_id == 'PROJ-3')
        self.assertEqual(broken.status, WORKSPACE_STATUS_ERRORED)

    def test_update_status_persists(self) -> None:
        self.manager.create(task_id='PROJ-1', task_summary='one')
        updated = self.manager.update_status('PROJ-1', WORKSPACE_STATUS_ACTIVE)
        self.assertIsNotNone(updated)
        assert updated is not None
        self.assertEqual(updated.status, WORKSPACE_STATUS_ACTIVE)
        record_again = self.manager.get('PROJ-1')
        assert record_again is not None
        self.assertEqual(record_again.status, WORKSPACE_STATUS_ACTIVE)

    def test_update_status_rejects_unknown_value(self) -> None:
        self.manager.create(task_id='PROJ-1')
        with self.assertRaisesRegex(ValueError, 'unknown workspace status'):
            self.manager.update_status('PROJ-1', 'totally-made-up')

    def test_update_repositories_persists(self) -> None:
        self.manager.create(task_id='PROJ-1', repository_ids=['a'])
        self.manager.update_repositories('PROJ-1', ['a', 'b'])
        record = self.manager.get('PROJ-1')
        assert record is not None
        self.assertEqual(record.repository_ids, ['a', 'b'])

    def test_update_resume_on_startup_persists(self) -> None:
        self.manager.create(task_id='PROJ-1')
        self.manager.update_resume_on_startup('PROJ-1', False)
        record = self.manager.get('PROJ-1')
        assert record is not None
        self.assertFalse(record.resume_on_startup)

    def test_delete_removes_the_folder(self) -> None:
        self.manager.create(task_id='PROJ-1', task_summary='one')
        self.assertTrue((self.root / 'PROJ-1').is_dir())
        self.manager.delete('PROJ-1')
        self.assertFalse((self.root / 'PROJ-1').exists())

    def test_delete_is_idempotent(self) -> None:
        # No folder yet — should be a no-op, not raise.
        self.manager.delete('NOPE-1')

    def test_max_parallel_tasks_clamped_to_one(self) -> None:
        manager = WorkspaceManager(root=self.root, max_parallel_tasks=0)
        self.assertEqual(manager.max_parallel_tasks, 1)
        manager = WorkspaceManager(root=self.root, max_parallel_tasks=-5)
        self.assertEqual(manager.max_parallel_tasks, 1)

    def test_safe_task_id_strips_path_separators(self) -> None:
        # Defensive: a malicious task id can't escape the workspace root.
        record = self.manager.create(task_id='evil/../escape')
        # The folder name has separators replaced with underscores.
        self.assertTrue((self.root / 'evil_.._escape').is_dir())
        self.assertEqual(record.task_id, 'evil_.._escape')

    def test_metadata_round_trip_preserves_fields(self) -> None:
        original = WorkspaceRecord(
            task_id='PROJ-9',
            task_summary='roundtrip test',
            status=WORKSPACE_STATUS_DONE,
            repository_ids=['repo1', 'repo2'],
            resume_on_startup=False,
            created_at_epoch=100.0,
            updated_at_epoch=200.0,
        )
        round_trip = WorkspaceRecord.from_dict(original.to_dict())
        self.assertEqual(round_trip, original)


if __name__ == '__main__':
    unittest.main()
