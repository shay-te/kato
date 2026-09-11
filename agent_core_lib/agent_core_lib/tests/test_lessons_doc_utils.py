"""Unit tests for ``read_lessons_file`` in ``lessons_doc_utils``.

Locks the spawn-time injection contract:
  * Empty / missing path returns ''.
  * Empty file returns ''.
  * Populated file returns the body wrapped in the directive template.
  * Timestamp header is stripped before injection.
  * Body cap protects the system-prompt budget.
  * Defensive read_text OSError branches degrade to '' (logged / silent).
"""

from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agent_core_lib.agent_core_lib.helpers.lessons_doc_utils import read_lessons_file


class ReadLessonsFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_dir = Path(self._tmp.name)

    def _write(self, content: str) -> Path:
        path = self.tmp_dir / 'lessons.md'
        path.write_text(content, encoding='utf-8')
        return path

    def test_empty_path_returns_empty(self) -> None:
        self.assertEqual(read_lessons_file(''), '')
        self.assertEqual(read_lessons_file('   '), '')

    def test_missing_file_returns_empty_silently(self) -> None:
        # Deliberately silent — lessons are optional. A missing file
        # is the normal "no lessons yet" case.
        self.assertEqual(
            read_lessons_file(str(self.tmp_dir / 'never-exists.md')),
            '',
        )

    def test_empty_file_returns_empty(self) -> None:
        path = self._write('')
        self.assertEqual(read_lessons_file(str(path)), '')

    def test_only_timestamp_header_returns_empty(self) -> None:
        path = self._write(
            '<!-- last_compacted: 2026-05-04T12:00:00+00:00 -->\n',
        )
        self.assertEqual(read_lessons_file(str(path)), '')

    def test_populated_file_returns_a_DIRECTIVE_not_the_body(self) -> None:
        # The lessons text is no longer pasted into the prompt. It used to be
        # (capped at 50K), and on Windows that pushed the spawn command line
        # to ~37.6K against a 32,767 limit — the CLI never started:
        # "[WinError 206] The filename or extension is too long".
        path = self._write(
            '<!-- last_compacted: 2026-05-04T12:00:00+00:00 -->\n\n'
            '- always use logger\n'
            '- never use print\n',
        )
        result = read_lessons_file(str(path))
        # Points at the file...
        self.assertIn(str(path), result)
        self.assertIn('Read tool', result)
        # ...and does NOT carry its contents.
        self.assertNotIn('- always use logger', result)
        self.assertNotIn('- never use print', result)
        self.assertNotIn('last_compacted', result)

    def test_the_prompt_stays_small_however_big_the_lessons_get(self) -> None:
        # The property that actually fixes the bug: prompt size no longer
        # tracks the lessons file at all, so it cannot grow back into the
        # command-line limit. The old cap merely deferred it — and silently
        # dropped everything past 50K.
        small = read_lessons_file(str(self._write('- one lesson\n')))
        self._path.unlink()
        huge = read_lessons_file(str(self._write('- ' + ('x' * 200_000) + '\n')))
        self.assertLess(len(huge), 1_000)
        # Identical but for the path — the size is fixed, not merely bounded.
        self.assertEqual(len(small), len(huge))

    def test_unreadable_file_logs_and_returns_empty(self) -> None:
        # Path points at a directory — stat OK but not a regular file.
        logger = MagicMock(spec=logging.Logger)
        result = read_lessons_file(str(self.tmp_dir), logger=logger)
        self.assertEqual(result, '')

    def test_unreadable_file_without_logger_returns_empty_silently(self) -> None:
        # No logger plumbed in — an unreadable path must still degrade
        # to '' instead of bubbling the OSError. Lessons are optional
        # observability, not a correctness gate.
        result = read_lessons_file(str(self.tmp_dir))
        self.assertEqual(result, '')


class ReadLessonsFileDefensiveBranchTests(unittest.TestCase):
    """``read_text`` raising OSError on a path that passes ``is_file``."""

    def test_read_text_oserror_without_logger_returns_empty(self) -> None:
        with patch(
            'pathlib.Path.read_text',
            side_effect=OSError('boom'),
        ), patch(
            'pathlib.Path.is_file',
            return_value=True,
        ), patch(
            'pathlib.Path.stat',
        ):
            result = read_lessons_file('/tmp/does-not-matter.md')
        self.assertEqual(result, '')

    def test_read_text_oserror_with_logger_warns_and_returns_empty(self) -> None:
        logger = logging.getLogger('test_lessons_doc_utils')
        with patch(
            'pathlib.Path.read_text',
            side_effect=OSError('boom'),
        ), patch(
            'pathlib.Path.is_file',
            return_value=True,
        ), patch(
            'pathlib.Path.stat',
        ), patch.object(logger, 'warning') as mock_warning:
            result = read_lessons_file(
                '/tmp/does-not-matter.md', logger=logger,
            )
        self.assertEqual(result, '')
        mock_warning.assert_called_once()


if __name__ == '__main__':
    unittest.main()
