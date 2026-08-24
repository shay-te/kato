"""Session records on disk: naming, atomic writes, deletion, and reload.

Two of these pin bugs that reached operators:

* the "task is back after I deleted it" resurrection, caused by deleting only
  the canonical lowercased filename while a legacy original-case file stayed
  behind for the next load to find;
* a tab that claimed to be live after a restart, with no subprocess behind it.

The rest guard the rule that one corrupt file must never cost the operator
every other tab.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agent_core_lib.agent_core_lib.session.record import (
    SESSION_STATUS_ACTIVE,
    SESSION_STATUS_DONE,
    SESSION_STATUS_TERMINATED,
    AgentSessionRecord,
)
from agent_core_lib.agent_core_lib.session.record_files import (
    delete_record,
    load_records,
    record_key,
    record_path,
    write_record,
)


class RecordKeyTests(unittest.TestCase):
    def test_case_and_padding_collapse_to_one_key(self) -> None:
        self.assertEqual(record_key('  PROJ-1 '), 'proj-1')
        self.assertEqual(record_key('proj-1'), record_key('PROJ-1'))

    def test_missing_id_is_empty_not_an_error(self) -> None:
        self.assertEqual(record_key(None), '')
        self.assertEqual(record_key(''), '')


class RecordPathTests(unittest.TestCase):
    def test_the_filename_is_the_lowercased_task_id(self) -> None:
        self.assertEqual(record_path(Path('/s'), 'PROJ-1').name, 'proj-1.json')

    def test_path_separators_cannot_escape_the_state_dir(self) -> None:
        # A task id is external input; a slash in it must not write elsewhere.
        path = record_path(Path('/s'), 'a/b')

        self.assertEqual(path.parent, Path('/s'))
        self.assertEqual(path.name, 'a_b.json')


class WriteAndLoadTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_dir = Path(self._tmp.name)
        self.logger = MagicMock()

    def test_a_written_record_reloads_field_for_field(self) -> None:
        record = AgentSessionRecord(
            task_id='PROJ-1', task_summary='fix it', agent_session_id='abc',
            status=SESSION_STATUS_DONE, cwd='/wks/PROJ-1',
            expected_branch='PROJ-1', context_used_tokens=1234,
            context_model='sonnet', context_baseline_tokens=900,
            previous_session_ids=['older'],
        )
        write_record(self.state_dir, record, logger=self.logger)

        loaded = load_records(self.state_dir, logger=self.logger)['proj-1']
        self.assertEqual(loaded.to_dict(), record.to_dict())

    def test_an_active_record_comes_back_terminated(self) -> None:
        # On startup the subprocess behind it is gone; a tab claiming to be
        # live with nothing behind it is worse than an honest dead one.
        write_record(
            self.state_dir,
            AgentSessionRecord(task_id='PROJ-1', status=SESSION_STATUS_ACTIVE),
            logger=self.logger,
        )

        loaded = load_records(self.state_dir, logger=self.logger)['proj-1']
        self.assertEqual(loaded.status, SESSION_STATUS_TERMINATED)

    def test_a_finished_record_keeps_its_status(self) -> None:
        write_record(
            self.state_dir,
            AgentSessionRecord(task_id='PROJ-1', status=SESSION_STATUS_DONE),
            logger=self.logger,
        )

        self.assertEqual(
            load_records(self.state_dir, logger=self.logger)['proj-1'].status,
            SESSION_STATUS_DONE,
        )

    def test_a_case_mismatched_id_finds_the_same_record(self) -> None:
        write_record(
            self.state_dir, AgentSessionRecord(task_id='PROJ-1'), logger=self.logger,
        )

        loaded = load_records(self.state_dir, logger=self.logger)
        self.assertIn(record_key('proj-1'), loaded)
        # The stored id keeps the case it was written with, for display.
        self.assertEqual(loaded['proj-1'].task_id, 'PROJ-1')

    def test_one_corrupt_file_does_not_cost_the_other_tabs(self) -> None:
        write_record(
            self.state_dir, AgentSessionRecord(task_id='GOOD-1'), logger=self.logger,
        )
        (self.state_dir / 'broken.json').write_text('{not json', encoding='utf-8')

        loaded = load_records(self.state_dir, logger=self.logger)

        self.assertIn('good-1', loaded)
        self.logger.warning.assert_called_once()

    def test_a_non_object_payload_is_skipped(self) -> None:
        (self.state_dir / 'list.json').write_text('[1, 2]', encoding='utf-8')

        self.assertEqual(load_records(self.state_dir, logger=self.logger), {})

    def test_a_record_without_a_task_id_is_skipped(self) -> None:
        (self.state_dir / 'blank.json').write_text(
            json.dumps({'task_id': '', 'status': SESSION_STATUS_DONE}), encoding='utf-8',
        )

        self.assertEqual(load_records(self.state_dir, logger=self.logger), {})

    def test_a_missing_state_dir_loads_nothing_and_does_not_raise(self) -> None:
        missing = self.state_dir / 'gone'

        self.assertEqual(load_records(missing, logger=self.logger), {})


class DeleteRecordTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_dir = Path(self._tmp.name)
        self.logger = MagicMock()

    def test_deleting_a_never_written_record_is_silent(self) -> None:
        delete_record(self.state_dir, 'nonexistent', logger=self.logger)

        self.logger.warning.assert_not_called()

    def test_a_legacy_original_case_file_is_deleted_too(self) -> None:
        # THE RESURRECTION BUG: records written before the lowercasing live
        # under the original-case name. Unlinking only the canonical path left
        # that file for the next load to find, and the deleted task's tab came
        # back on every restart.
        legacy = self.state_dir / 'UNA-1201.json'
        legacy.write_text(json.dumps({'task_id': 'UNA-1201'}), encoding='utf-8')
        write_record(
            self.state_dir, AgentSessionRecord(task_id='UNA-1201'), logger=self.logger,
        )
        canonical = record_path(self.state_dir, 'UNA-1201')
        self.assertTrue(canonical.is_file())

        delete_record(self.state_dir, 'UNA-1201', logger=self.logger)

        self.assertFalse(canonical.exists())
        self.assertFalse(legacy.exists())
        self.assertEqual(load_records(self.state_dir, logger=self.logger), {})

    def test_a_failed_directory_listing_still_deletes_the_canonical_file(self) -> None:
        write_record(
            self.state_dir, AgentSessionRecord(task_id='PROJ-G'), logger=self.logger,
        )
        canonical = record_path(self.state_dir, 'PROJ-G')

        with patch.object(Path, 'glob', side_effect=OSError('listing failed')):
            delete_record(self.state_dir, 'PROJ-G', logger=self.logger)

        self.assertFalse(canonical.is_file())

    def test_an_unlink_failure_is_logged_not_raised(self) -> None:
        write_record(
            self.state_dir, AgentSessionRecord(task_id='PROJ-X'), logger=self.logger,
        )

        with patch.object(Path, 'unlink', side_effect=PermissionError('locked')):
            delete_record(self.state_dir, 'PROJ-X', logger=self.logger)

        self.logger.warning.assert_called_once()


if __name__ == '__main__':
    unittest.main()
