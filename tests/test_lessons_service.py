"""Unit tests for ``LessonsService``.

The LLM is injected as ``llm_one_shot``, so these tests don't spawn
any Claude subprocesses. They lock the policy decisions:

  * Junk responses (``NO_LESSON``, empty, vague) are dropped.
  * Real bullet responses are saved to the per-task file.
  * Same-task re-extraction overwrites (no duplicates).
  * Compact merges pending into global, deletes per-task, updates
    timestamp, runs at most once concurrently.
  * ``should_compact`` honours the 24h gate.
"""

from __future__ import annotations

import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kato_core_lib.data_layers.data_access.lessons_data_access import (
    LessonsDataAccess,
)
from kato_core_lib.data_layers.service.lessons_service import LessonsService


class _FakeLLM:
    """Records calls and returns scripted responses in order."""

    def __init__(self, *responses) -> None:
        self._responses = list(responses)
        self.calls: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.calls.append(prompt)
        if not self._responses:
            return ''
        return self._responses.pop(0)


class LessonsServiceExtractionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_dir = Path(self._tmp.name)
        self.dao = LessonsDataAccess(self.state_dir)

    def test_real_bullet_lesson_is_saved(self) -> None:
        llm = _FakeLLM('- always use logger.exception for caught errors')
        service = LessonsService(self.dao, llm)
        result = service.extract_and_save('PROJ-1', 'task did X')
        self.assertEqual(result, '- always use logger.exception for caught errors')
        self.assertEqual(
            self.dao.read_per_task('PROJ-1'),
            '- always use logger.exception for caught errors\n',
        )

    def test_no_lesson_marker_clears_per_task_file(self) -> None:
        # First extraction saves something; second returns NO_LESSON
        # and must remove the previously-saved lesson so a re-run
        # producing nothing doesn't leave stale text.
        self.dao.write_per_task('PROJ-1', '- old lesson')
        llm = _FakeLLM('NO_LESSON')
        service = LessonsService(self.dao, llm)
        result = service.extract_and_save('PROJ-1', 'task context')
        self.assertEqual(result, '')
        self.assertIsNone(self.dao.read_per_task('PROJ-1'))

    def test_empty_response_is_treated_as_no_lesson(self) -> None:
        llm = _FakeLLM('')
        service = LessonsService(self.dao, llm)
        result = service.extract_and_save('PROJ-1', 'ctx')
        self.assertEqual(result, '')
        self.assertIsNone(self.dao.read_per_task('PROJ-1'))

    def test_response_without_bullet_is_treated_as_no_lesson(self) -> None:
        # Defends against a response like "Yes, the lesson is to be careful."
        # — must not be saved as a lesson.
        llm = _FakeLLM('Be careful when editing files.')
        service = LessonsService(self.dao, llm)
        result = service.extract_and_save('PROJ-1', 'ctx')
        self.assertEqual(result, '')
        self.assertIsNone(self.dao.read_per_task('PROJ-1'))

    def test_response_with_extra_lines_keeps_only_first_bullet(self) -> None:
        llm = _FakeLLM(
            'Some preamble.\n- the actual rule\n- a second one we should ignore',
        )
        service = LessonsService(self.dao, llm)
        result = service.extract_and_save('PROJ-1', 'ctx')
        self.assertEqual(result, '- the actual rule')

    def test_extraction_failure_is_swallowed(self) -> None:
        def boom(_prompt: str) -> str:
            raise RuntimeError('LLM down')

        service = LessonsService(self.dao, boom)
        # Should not raise.
        result = service.extract_and_save('PROJ-1', 'ctx')
        self.assertEqual(result, '')
        # Pre-existing per-task file should be left alone on extraction
        # failure (different from the NO_LESSON case).
        self.dao.write_per_task('PROJ-2', '- old')
        result = service.extract_and_save('PROJ-2', 'ctx')
        self.assertEqual(result, '')
        self.assertEqual(self.dao.read_per_task('PROJ-2'), '- old\n')

    def test_empty_task_id_is_rejected(self) -> None:
        llm = _FakeLLM('- a lesson')
        service = LessonsService(self.dao, llm)
        result = service.extract_and_save('', 'ctx')
        self.assertEqual(result, '')
        self.assertEqual(llm.calls, [])  # Never reached the LLM.

    def test_same_task_re_extraction_overwrites(self) -> None:
        llm = _FakeLLM('- first', '- second')
        service = LessonsService(self.dao, llm)
        service.extract_and_save('PROJ-1', 'ctx 1')
        service.extract_and_save('PROJ-1', 'ctx 2')
        # The per-task file was overwritten — only ONE lesson survives.
        self.assertEqual(self.dao.read_per_task('PROJ-1'), '- second\n')


class LessonsServiceCompactTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_dir = Path(self._tmp.name)
        self.dao = LessonsDataAccess(self.state_dir)

    def test_compact_merges_pending_into_global_and_deletes_per_task(self) -> None:
        self.dao.write_per_task('PROJ-1', '- lesson A')
        self.dao.write_per_task('PROJ-2', '- lesson B')
        llm = _FakeLLM('- lesson A\n- lesson B')
        service = LessonsService(self.dao, llm)

        ok = service.compact()

        self.assertTrue(ok)
        body = self.dao.read_global_body()
        self.assertIn('- lesson A', body)
        self.assertIn('- lesson B', body)
        # Per-task files removed.
        self.assertEqual(self.dao.list_per_task_ids(), [])
        # Timestamp set.
        self.assertIsNotNone(self.dao.last_compacted_at())

    def test_compact_with_no_pending_refreshes_timestamp_only(self) -> None:
        # No pending lessons but existing core. Compact should just
        # bump the timestamp — no LLM call needed.
        self.dao.write_global('- existing core lesson')
        llm = _FakeLLM('- this should never be returned')
        service = LessonsService(self.dao, llm)

        ok = service.compact()

        self.assertTrue(ok)
        self.assertEqual(llm.calls, [], 'no LLM call when nothing is pending')
        self.assertIn('- existing core lesson', self.dao.read_global_body())

    def test_compact_with_no_pending_and_no_global_is_noop(self) -> None:
        llm = _FakeLLM()
        service = LessonsService(self.dao, llm)
        self.assertFalse(service.compact())
        self.assertEqual(llm.calls, [])

    def test_compact_failure_leaves_files_untouched(self) -> None:
        self.dao.write_per_task('PROJ-1', '- a')
        original_global = '- existing'
        self.dao.write_global(original_global)

        def boom(_prompt: str) -> str:
            raise RuntimeError('LLM down')

        service = LessonsService(self.dao, boom)
        ok = service.compact()

        self.assertFalse(ok)
        # Pending file still there.
        self.assertEqual(self.dao.read_per_task('PROJ-1'), '- a\n')
        # Global unchanged.
        self.assertIn('- existing', self.dao.read_global_body())

    def test_compact_empty_response_leaves_files_untouched(self) -> None:
        self.dao.write_per_task('PROJ-1', '- a')
        llm = _FakeLLM('')  # Whitespace-only / empty.
        service = LessonsService(self.dao, llm)

        ok = service.compact()

        self.assertFalse(ok)
        self.assertEqual(self.dao.read_per_task('PROJ-1'), '- a\n')

    def test_concurrent_compact_calls_serialize(self) -> None:
        self.dao.write_per_task('PROJ-1', '- a')
        # Slow LLM so we can race two callers.
        gate = threading.Event()
        call_count = [0]

        def slow_llm(_prompt: str) -> str:
            call_count[0] += 1
            gate.wait(timeout=2.0)
            return '- merged'

        service = LessonsService(self.dao, slow_llm)
        results: list[bool] = []
        threads = [
            threading.Thread(target=lambda: results.append(service.compact()))
            for _ in range(3)
        ]
        for t in threads:
            t.start()
        # Let the first caller pass through; others should bail with False.
        time.sleep(0.05)
        gate.set()
        for t in threads:
            t.join(timeout=3.0)

        # Exactly one compact actually ran the LLM.
        self.assertEqual(call_count[0], 1)
        self.assertEqual(sum(results), 1)


class LessonsServiceShouldCompactTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_dir = Path(self._tmp.name)
        self.dao = LessonsDataAccess(self.state_dir)
        self.service = LessonsService(self.dao, _FakeLLM())

    def test_no_history_no_pending_returns_false(self) -> None:
        self.assertFalse(self.service.should_compact())

    def test_no_history_with_pending_returns_true(self) -> None:
        self.dao.write_per_task('PROJ-1', '- a')
        self.assertTrue(self.service.should_compact())

    def test_recent_compact_returns_false(self) -> None:
        recent = datetime.now(timezone.utc) - timedelta(hours=1)
        self.dao.write_global('- a', compacted_at=recent)
        self.assertFalse(self.service.should_compact())

    def test_old_compact_returns_true(self) -> None:
        old = datetime.now(timezone.utc) - timedelta(hours=25)
        self.dao.write_global('- a', compacted_at=old)
        self.assertTrue(self.service.should_compact())

    def test_custom_interval_honoured(self) -> None:
        service = LessonsService(
            self.dao, _FakeLLM(), compact_interval=timedelta(minutes=5),
        )
        recent = datetime.now(timezone.utc) - timedelta(minutes=10)
        self.dao.write_global('- a', compacted_at=recent)
        self.assertTrue(service.should_compact())


class LessonsServiceComposeAddendumTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_dir = Path(self._tmp.name)
        self.dao = LessonsDataAccess(self.state_dir)
        self.service = LessonsService(self.dao, _FakeLLM())

    def test_returns_empty_when_no_global(self) -> None:
        self.assertEqual(self.service.compose_addendum(), '')

    def test_returns_body_without_timestamp_header(self) -> None:
        self.dao.write_global('- core lesson 1\n- core lesson 2')
        addendum = self.service.compose_addendum()
        self.assertNotIn('last_compacted', addendum)
        self.assertIn('- core lesson 1', addendum)


if __name__ == '__main__':
    unittest.main()
