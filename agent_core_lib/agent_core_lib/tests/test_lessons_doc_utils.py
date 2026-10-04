"""Unit tests for ``read_lessons_file`` in ``lessons_doc_utils``.

Locks the spawn-time injection contract:
  * Empty / missing path returns ''.
  * Empty file returns ''.
  * Populated file returns a directive naming the file, never its body.
  * The same directive hands the agent the upkeep of the file and — past
    the size budget — requires the edit to shrink it.
  * Defensive read_text OSError branches degrade to '' (logged / silent).
"""

from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agent_core_lib.agent_core_lib.helpers.lessons_doc_utils import (
    LESSONS_BUDGET_CHARS,
    read_lessons_file,
)


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
        # tracks the lessons file, so it cannot grow back into the
        # command-line limit. The old cap merely deferred it — and silently
        # dropped everything past 50K.
        # Two DIFFERENT files: ``read_lessons_file`` caches per path+mtime, so
        # rewriting one in the same tick could serve a stale render and make
        # this pass for the wrong reason.
        small_path = self.tmp_dir / 'small.md'
        small_path.write_text('- one lesson\n', encoding='utf-8')
        huge_path = self.tmp_dir / 'huge.md'
        huge_path.write_text('- ' + ('x' * 2_000_000) + '\n', encoding='utf-8')

        small = read_lessons_file(str(small_path))
        huge = read_lessons_file(str(huge_path))

        # Bounded, not proportional: a file 150,000 times the size adds only
        # the two sentences that say it is too big.
        self.assertLess(len(huge), 4_000)
        self.assertLess(len(huge) - len(small), 600)

    def test_the_agent_is_told_to_keep_the_file_not_only_read_it(self) -> None:
        # ONE document. The agent that just did the work is the editor of
        # what the work taught, so the same directive that orders the read
        # also hands over the upkeep — there is no second document for that.
        result = read_lessons_file(str(self._write('- a rule\n')))
        self.assertIn('keep it accurate AND small', result)
        self.assertIn('the operator corrected you', result)
        self.assertIn('REPLACE it', result)
        self.assertIn('Edit at most once per task', result)
        self.assertIn('NEVER run git', result)

    def test_within_budget_the_edit_may_be_net_neutral(self) -> None:
        result = read_lessons_file(str(self._write('- a rule\n')))
        self.assertIn('net-neutral or net-shorter', result)
        self.assertNotIn('OVER its size budget', result)
        self.assertNotIn('consecutive chunks', result)

    def test_over_budget_the_edit_must_shrink_the_file(self) -> None:
        # The file is re-read in full by every task, so size is the one thing
        # that makes every later task worse. Nothing used to bound it.
        path = self.tmp_dir / 'big.md'
        path.write_text('x' * (LESSONS_BUDGET_CHARS + 1), encoding='utf-8')
        result = read_lessons_file(str(path))
        self.assertIn('OVER its size budget (80 KB against 80 KB)', result)
        self.assertIn('must leave it SMALLER', result)
        self.assertNotIn('net-neutral', result)
        # More than one Read returns: say so, or the first page passes for
        # the whole file.
        self.assertIn('read ALL of it, in consecutive chunks', result)

    def test_exactly_at_budget_is_still_within_it(self) -> None:
        path = self.tmp_dir / 'edge.md'
        path.write_text('x' * LESSONS_BUDGET_CHARS, encoding='utf-8')
        self.assertNotIn('OVER its size budget', read_lessons_file(str(path)))

    def test_a_legacy_header_does_not_count_toward_the_budget(self) -> None:
        path = self.tmp_dir / 'stamped.md'
        path.write_text(
            '<!-- last_compacted: 2026-05-04T12:00:00+00:00 -->\n\n'
            + 'x' * LESSONS_BUDGET_CHARS,
            encoding='utf-8',
        )
        self.assertNotIn('OVER its size budget', read_lessons_file(str(path)))

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
