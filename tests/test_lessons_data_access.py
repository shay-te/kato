"""Unit tests for ``LessonsDataAccess``.

Locks the file-layout contract:
  * Per-task lessons live at ``state_dir/lessons/<task-id>.md``.
  * Global lesson file lives at ``state_dir/lessons.md``.
  * The lessons document is written exactly as given — nothing is stamped
    onto it — and can be copied aside before it is replaced.
  * Path-traversal characters in task ids are rejected (no escape from
    the per-task dir).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kato_core_lib.data_layers.data_access.lessons_data_access import (
    LessonsDataAccess,
    strip_timestamp_header,
)


class LessonsDataAccessTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_dir = Path(self._tmp.name)
        self.dao = LessonsDataAccess(self.state_dir)

    # ----- per-task -----

    def test_write_then_read_per_task_round_trip(self) -> None:
        ok = self.dao.write_per_task('PROJ-1', '- always use logger\n')
        self.assertTrue(ok)
        self.assertEqual(
            self.dao.read_per_task('PROJ-1'),
            '- always use logger\n',
        )

    def test_write_per_task_appends_trailing_newline_if_missing(self) -> None:
        self.dao.write_per_task('PROJ-1', '- one line no newline')
        self.assertTrue(
            (self.state_dir / 'lessons' / 'PROJ-1.md').read_text().endswith('\n'),
        )

    def test_write_per_task_overwrites_existing(self) -> None:
        self.dao.write_per_task('PROJ-1', '- old')
        self.dao.write_per_task('PROJ-1', '- new')
        self.assertEqual(self.dao.read_per_task('PROJ-1'), '- new\n')

    def test_read_per_task_returns_none_when_missing(self) -> None:
        self.assertIsNone(self.dao.read_per_task('NEVER-EXISTED'))

    def test_delete_per_task_removes_file(self) -> None:
        self.dao.write_per_task('PROJ-1', '- a')
        self.dao.delete_per_task('PROJ-1')
        self.assertIsNone(self.dao.read_per_task('PROJ-1'))

    def test_delete_per_task_is_noop_when_missing(self) -> None:
        # Should not raise.
        self.dao.delete_per_task('NEVER-EXISTED')

    def test_list_per_task_ids_returns_sorted(self) -> None:
        self.dao.write_per_task('PROJ-3', '- a')
        self.dao.write_per_task('PROJ-1', '- b')
        self.dao.write_per_task('PROJ-2', '- c')
        self.assertEqual(
            self.dao.list_per_task_ids(),
            ['PROJ-1', 'PROJ-2', 'PROJ-3'],
        )

    def test_list_per_task_ids_empty_when_dir_missing(self) -> None:
        self.assertEqual(self.dao.list_per_task_ids(), [])

    def test_read_all_per_task_returns_dict(self) -> None:
        self.dao.write_per_task('PROJ-1', '- a')
        self.dao.write_per_task('PROJ-2', '- b')
        all_lessons = self.dao.read_all_per_task()
        self.assertEqual(set(all_lessons.keys()), {'PROJ-1', 'PROJ-2'})
        self.assertEqual(all_lessons['PROJ-1'], '- a\n')

    def test_list_per_task_ids_skips_non_md_files_and_subdirectories(self) -> None:
        # Branch 171->170: ``if entry.is_file() and entry.suffix == '.md':``
        # false branch — non-md files and subdirectories must be
        # silently skipped, not appended to the result list.
        self.dao.write_per_task('PROJ-1', '- a')
        per_task_dir = self.state_dir / 'lessons'
        # A stray non-md file (e.g. a backup or unrelated artifact).
        (per_task_dir / 'README.txt').write_text('noise', encoding='utf-8')
        # A stray subdirectory (e.g. an attic for old lessons).
        (per_task_dir / 'archive').mkdir()

        self.assertEqual(self.dao.list_per_task_ids(), ['PROJ-1'])

    def test_read_all_per_task_skips_entries_whose_content_is_none(self) -> None:
        # Branch 180->178: ``if content is not None:`` false branch —
        # when ``read_per_task`` returns None (e.g. file disappeared
        # between the listing and the read), the task id must be
        # silently skipped in the resulting dict.
        self.dao.write_per_task('PROJ-1', '- a')
        self.dao.write_per_task('PROJ-2', '- b')

        original_read_per_task = self.dao.read_per_task

        def _fake_read(task_id: str):
            if task_id == 'PROJ-1':
                return None
            return original_read_per_task(task_id)

        self.dao.read_per_task = _fake_read  # type: ignore[method-assign]

        result = self.dao.read_all_per_task()
        self.assertEqual(set(result.keys()), {'PROJ-2'})
        self.assertEqual(result['PROJ-2'], '- b\n')

    def test_path_traversal_task_id_is_rejected(self) -> None:
        # Forbidden characters: /, \, .., null. None of these may be
        # used to escape the per-task directory.
        for bad in ('../escape', '/etc/passwd', 'a\\b', 'a\x00b', '.', '..'):
            ok = self.dao.write_per_task(bad, '- malicious')
            self.assertFalse(ok, f'should reject task id {bad!r}')
        self.assertEqual(self.dao.list_per_task_ids(), [])

    def test_empty_or_blank_task_id_is_rejected(self) -> None:
        self.assertFalse(self.dao.write_per_task('', '- a'))
        self.assertFalse(self.dao.write_per_task('   ', '- a'))
        self.assertIsNone(self.dao.read_per_task(''))

    # ----- candidates -----

    def test_write_then_read_candidate_round_trip(self) -> None:
        ok = self.dao.write_candidate('task__PROJ-1__prompt__abc', '- rule')
        self.assertTrue(ok)
        self.assertEqual(
            self.dao.read_candidate('task__PROJ-1__prompt__abc'),
            '- rule\n',
        )

    def test_list_candidate_ids_filters_by_prefix(self) -> None:
        self.dao.write_candidate('task__PROJ-1__prompt__a', '- a')
        self.dao.write_candidate('task__PROJ-1__prompt__b', '- b')
        self.dao.write_candidate('comment__PROJ-1__c1__a', '- c')

        self.assertEqual(
            self.dao.list_candidate_ids('task__PROJ-1__'),
            ['task__PROJ-1__prompt__a', 'task__PROJ-1__prompt__b'],
        )

    def test_delete_candidate_removes_file(self) -> None:
        self.dao.write_candidate('candidate-1', '- a')
        self.dao.delete_candidate('candidate-1')
        self.assertIsNone(self.dao.read_candidate('candidate-1'))

    def test_invalid_candidate_id_is_rejected(self) -> None:
        self.assertFalse(self.dao.write_candidate('../escape', '- malicious'))
        self.assertEqual(self.dao.list_candidate_ids(), [])

    # ----- global -----

    def test_write_then_read_global_round_trip(self) -> None:
        ok = self.dao.write_global('- core lesson 1\n- core lesson 2')
        self.assertTrue(ok)
        body = self.dao.read_global_body()
        self.assertIn('- core lesson 1', body)
        self.assertIn('- core lesson 2', body)

    def test_write_global_writes_the_document_as_given(self) -> None:
        # The agent curates this file. Nothing is stamped onto it: the
        # ``last_compacted`` header belonged to a step that no longer exists.
        self.dao.write_global('# Lessons\n\n- a')
        self.assertEqual(self.dao.read_global(), '# Lessons\n\n- a\n')

    def test_read_global_body_strips_a_header_left_by_an_older_version(self) -> None:
        (self.state_dir / 'lessons.md').write_text(
            '<!-- last_compacted: 2026-05-04T12:00:00+00:00 -->\n\n- core 1\n- core 2\n',
            encoding='utf-8',
        )
        body = self.dao.read_global_body()
        self.assertNotIn('last_compacted', body)
        self.assertIn('- core 1', body)

    def test_backup_copies_the_document_beside_itself(self) -> None:
        self.dao.write_global('- a')
        backup = self.dao.backup_global('bak-1')
        self.assertEqual(backup, self.state_dir / 'lessons.md.bak-1')
        self.assertEqual(backup.read_text(encoding='utf-8'), '- a\n')
        # A copy, not a move.
        self.assertEqual(self.dao.read_global(), '- a\n')

    def test_backup_of_an_empty_document_is_nothing(self) -> None:
        self.assertIsNone(self.dao.backup_global('bak-1'))
        self.assertEqual(list(self.state_dir.glob('lessons.md.*')), [])

    def test_backup_reports_a_failed_copy(self) -> None:
        self.dao.write_global('- a')
        with patch(
            'kato_core_lib.data_layers.data_access.lessons_data_access'
            '.atomic_write_text', return_value=False,
        ):
            self.assertIsNone(self.dao.backup_global('bak-1'))

    # ----- adoption marker -----

    def test_adoption_marker_round_trip(self) -> None:
        self.assertEqual(self.dao.adopted_legacy_path(), '')
        self.assertTrue(self.dao.mark_legacy_adopted('/docs/architecture.md'))
        self.assertEqual(self.dao.adopted_legacy_path(), '/docs/architecture.md')
        # Kept beside the document, never inside it — the agent edits that file.
        self.assertEqual(self.dao.read_global(), '')


class StripTimestampHeaderTests(unittest.TestCase):
    def test_strips_when_present(self) -> None:
        text = (
            '<!-- last_compacted: 2026-05-04T12:00:00+00:00 -->\n\n- a\n'
        )
        # ``splitlines`` drops the trailing newline; that's fine for
        # body text destined for system-prompt injection.
        self.assertEqual(strip_timestamp_header(text), '- a')

    def test_passes_through_when_absent(self) -> None:
        self.assertEqual(strip_timestamp_header('- a\n'), '- a\n')

    def test_empty_input(self) -> None:
        self.assertEqual(strip_timestamp_header(''), '')

    def test_only_header(self) -> None:
        self.assertEqual(
            strip_timestamp_header('<!-- last_compacted: 2026-05-04T12:00:00+00:00 -->'),
            '',
        )


if __name__ == '__main__':
    unittest.main()
