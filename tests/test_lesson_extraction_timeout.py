"""Lesson extraction has its own time budget, a bounded prompt, and a quiet failure.

UNA-3416 (2026-11-13): ``lesson extraction failed … timed out after 120 seconds``
with a full traceback. The prompt was small (a 453-character ticket) and the
same one-shot takes ~12s on an idle API; it ran beside the Done push, the PR,
Slack and other agents, while the Claude CLI retried a busy API by itself.

Real ``LessonsService`` / ``TaskLessonService`` / ``LessonsDataAccess`` on disk,
and real ``claude -p`` subprocesses — a fake ``claude`` script that answers, or
one that sleeps past the timeout. No mocks; ``patch.dict(os.environ)`` only
keeps the environment the build writes to from leaking.
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from omegaconf import OmegaConf

from claude_core_lib.claude_core_lib.helpers.one_shot_utils import make_one_shot
from kato_core_lib.data_layers.data_access.lessons_data_access import LessonsDataAccess
from kato_core_lib.data_layers.service.lessons_service import (
    EXTRACTION_CONTEXT_MAX_CHARS,
    LessonsService,
    bounded_context,
)
from kato_core_lib.data_layers.service.task_lesson_service import (
    TaskLessonService,
    task_lesson_candidate_id,
)
from kato_core_lib.kato_core_lib import _LESSON_EXTRACTION_TIMEOUT_SECONDS, KatoCoreLib


def _script(folder: Path, name: str, body: str) -> str:
    path = folder / name
    path.write_text(f'#!{sys.executable}\n{body}', encoding='utf-8')
    path.chmod(0o755)
    return str(path)


_ANSWERS = (
    'import os, sys\n'
    'prompt = sys.stdin.read()\n'
    'saved = os.environ.get("FAKE_CLAUDE_PROMPT")\n'
    'if saved:\n'
    '    open(saved, "w", encoding="utf-8").write(prompt)\n'
    'print("- a concrete rule the task taught")\n'
)
_SLEEPS = 'import sys, time\nsys.stdin.read()\ntime.sleep(5)\n'


def _closure(function) -> dict:
    return dict(zip(function.__code__.co_freevars, (c.cell_contents for c in function.__closure__)))


class _Folder(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.dao = LessonsDataAccess(self.root / 'state')
        self.logger = logging.getLogger(f'test_lesson_extraction_timeout.{self.id()}')

    def _slow_one_shot(self):
        return make_one_shot(binary=_script(self.root, 'slow-claude', _SLEEPS), timeout_seconds=1)


class KatoWiresItsOwnBudgetTests(_Folder):
    def test_kato_builds_extraction_with_its_own_timeout_and_it_really_runs(self) -> None:
        binary = _script(self.root, 'claude', _ANSWERS)
        prompt_file = self.root / 'prompt.txt'
        cfg = OmegaConf.create({'claude': {
            'binary': binary, 'model': '', 'lessons_path': str(self.root / 'lessons.md'),
        }})
        with patch.dict(os.environ, {
            'KATO_LESSON_EXTRACTION_CWD': str(self.root / 'scratch'),
            'FAKE_CLAUDE_PROMPT': str(prompt_file),
            'KATO_ARCHITECTURE_DOC_PATH': '',
        }):
            service = KatoCoreLib._build_lessons_service(KatoCoreLib.__new__(KatoCoreLib), cfg)
            self.assertEqual(_closure(service._llm_one_shot)['timeout_seconds'], 600)
            self.assertEqual(_LESSON_EXTRACTION_TIMEOUT_SECONDS, 600)
            lesson = service.extract_and_save('UNA-1', 'Task summary: x\n' + 'y' * 50_000)
        self.assertEqual(lesson, '- a concrete rule the task taught')
        sent = prompt_file.read_text(encoding='utf-8')
        self.assertLess(len(sent), EXTRACTION_CONTEXT_MAX_CHARS + 2_000)
        self.assertIn('characters left out', sent)


class OneShotThatRunsOutOfTimeTests(_Folder):
    def test_one_warning_line_no_traceback_and_the_per_task_file_is_kept(self) -> None:
        service = LessonsService(self.dao, self._slow_one_shot(), logger=self.logger)
        self.dao.write_per_task('UNA-1', '- an earlier lesson')
        with self.assertLogs(self.logger, level='WARNING') as logs:
            self.assertEqual(service.extract_and_save('UNA-1', 'context'), '')
        self.assertEqual(len(logs.records), 1)
        record = logs.records[0]
        self.assertEqual(record.levelno, logging.WARNING)
        self.assertIsNone(record.exc_info)
        self.assertIn('could not extract the lesson of task UNA-1', record.getMessage())
        self.assertIn('did not finish within 1s', record.getMessage())
        self.assertEqual(self.dao.read_per_task('UNA-1'), '- an earlier lesson\n')

    def test_a_candidate_s_timeout_is_one_warning_too(self) -> None:
        service = LessonsService(self.dao, self._slow_one_shot(), logger=self.logger)
        with self.assertLogs(self.logger, level='WARNING') as logs:
            self.assertEqual(service.extract_candidate_and_save('task__UNA-1__prompt__a', 'ctx'), '')
        self.assertEqual(len(logs.records), 1)
        self.assertIsNone(logs.records[0].exc_info)
        self.assertIn('could not extract candidate lesson task__UNA-1__prompt__a', logs.records[0].getMessage())

    def test_any_other_failure_is_a_bug_and_keeps_its_traceback(self) -> None:
        def broken(_prompt: str) -> str:
            raise KeyError('a bug')

        service = LessonsService(self.dao, broken, logger=self.logger)
        with self.assertLogs(self.logger, level='ERROR') as logs:
            self.assertEqual(service.extract_and_save('UNA-1', 'context'), '')
        self.assertIsNotNone(logs.records[0].exc_info)

    def test_lessons_staged_for_the_task_survive_its_extraction_timing_out(self) -> None:
        candidate = task_lesson_candidate_id('UNA-1', 'prompt')
        self.dao.write_candidate(candidate, '- a rule the operator taught')
        lessons = LessonsService(self.dao, self._slow_one_shot(), logger=self.logger)
        with self.assertLogs(self.logger, level='WARNING'):
            TaskLessonService(lessons_service=lessons, logger=self.logger).capture_task_lesson(
                'UNA-1', 'context',
            )
            for thread in threading.enumerate():
                if thread.name == 'kato-lesson-extract-UNA-1':
                    thread.join(15)
        # Promoted before the extraction ran: a pending lesson, filed once an
        # editor runs (none here, so it waits), never lost to the timeout.
        self.assertEqual(self.dao.read_per_task(candidate), '- a rule the operator taught\n')
        self.assertIsNone(self.dao.read_per_task('UNA-1'))


class BoundedContextTests(unittest.TestCase):
    def test_a_short_context_is_sent_as_it_is(self) -> None:
        self.assertEqual(bounded_context('what happened'), 'what happened')
        self.assertEqual(bounded_context(None), '')
        exact = 'z' * EXTRACTION_CONTEXT_MAX_CHARS
        self.assertEqual(bounded_context(exact), exact)

    def test_a_long_context_keeps_its_start_and_its_end(self) -> None:
        text = 'S' + 'm' * 100_000 + 'E'
        bounded = bounded_context(text)
        self.assertTrue(bounded.startswith('S'))
        self.assertTrue(bounded.endswith('E'))
        self.assertIn('[… 80002 characters left out …]', bounded)
        self.assertLess(len(bounded), EXTRACTION_CONTEXT_MAX_CHARS + 100)

    def test_both_prompts_are_bounded(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        service = LessonsService(LessonsDataAccess(Path(tmp.name)), lambda p: '')
        long = 'q' * 60_000
        self.assertIn('characters left out', service._build_extraction_prompt('UNA-1', long))
        self.assertIn('characters left out', service._build_candidate_prompt('c1', long))


if __name__ == '__main__':
    unittest.main()
