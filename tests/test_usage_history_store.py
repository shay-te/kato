"""Tests for the kept usage of deleted tasks.

Deleting a task deletes its transcripts, which the Usage dialog counts from, so
this file is the only trace of what a deleted task used. It must survive a
restart, never lose an earlier entry to a later one, and never take the dialog
down when the file is damaged.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kato_core_lib.helpers import usage_history_store as store


def _entry(task_id: str, total: int = 10) -> dict:
    return {'task_id': task_id, 'usage': {'total': total}, 'deleted_at': '2026-10-19T08:00:00Z'}


class UsageHistoryStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / 'sub' / 'usage_history.json'
        ctx = patch.dict(os.environ, {'KATO_USAGE_HISTORY_PATH': str(self.path)})
        ctx.start()
        self.addCleanup(ctx.stop)

    def test_empty_when_no_file(self) -> None:
        self.assertEqual(store.deleted_task_usage(), [])

    def test_an_entry_is_kept_on_disk_and_read_back(self) -> None:
        store.record_deleted_task_usage(_entry('UNA-1'))
        # Parent dir is created on write; the file is the source of truth.
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))[0]['task_id'], 'UNA-1')
        self.assertEqual(store.deleted_task_usage(), [_entry('UNA-1')])

    def test_a_later_entry_never_replaces_an_earlier_one(self) -> None:
        store.record_deleted_task_usage(_entry('UNA-1'))
        store.record_deleted_task_usage(_entry('UNA-2'))
        # The same id deleted twice (re-adopted, worked on, deleted again) is
        # two pieces of work, so two entries.
        store.record_deleted_task_usage(_entry('UNA-1', total=99))
        self.assertEqual(
            [(e['task_id'], e['usage']['total']) for e in store.deleted_task_usage()],
            [('UNA-1', 10), ('UNA-2', 10), ('UNA-1', 99)],
        )

    def test_a_damaged_file_reads_as_empty_not_as_an_error(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_text('{not json', encoding='utf-8')
        self.assertEqual(store.deleted_task_usage(), [])
        self.path.write_text('{"task_id": "UNA-1"}', encoding='utf-8')
        self.assertEqual(store.deleted_task_usage(), [])

    def test_entries_that_are_not_usage_records_are_skipped(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps([
            _entry('UNA-1'), 'junk', {'task_id': '  ', 'usage': {}},
            {'task_id': 'UNA-2'}, {'task_id': 'UNA-3', 'usage': 'many'},
        ]), encoding='utf-8')
        self.assertEqual([e['task_id'] for e in store.deleted_task_usage()], ['UNA-1'])

    def test_recording_after_a_damaged_file_starts_a_fresh_list(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_text('[[[', encoding='utf-8')
        store.record_deleted_task_usage(_entry('UNA-5'))
        self.assertEqual([e['task_id'] for e in store.deleted_task_usage()], ['UNA-5'])


if __name__ == '__main__':
    unittest.main()
