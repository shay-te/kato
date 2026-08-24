import time
import unittest
from unittest.mock import MagicMock

from kato_core_lib.data_layers.service.task_lesson_service import (
    TaskLessonService,
    is_trivial_lesson_prompt,
)


def _settle() -> None:
    """Give the daemon worker a moment — every capture is asynchronous."""
    time.sleep(0.05)


class IsTrivialLessonPromptTests(unittest.TestCase):
    def test_acks_and_continuations_carry_no_lesson(self) -> None:
        for trivial in ('continue', '  CONTINUE  ', 'ok', 'yes', '', '👍',
                        'Please continue from where you left off.'):
            self.assertTrue(is_trivial_lesson_prompt(trivial), trivial)

    def test_a_real_instruction_is_not_trivial(self) -> None:
        for real in ('always run dedup before finishing',
                     'fix the failing test in module X'):
            self.assertFalse(is_trivial_lesson_prompt(real), real)


class WithoutALessonsServiceTests(unittest.TestCase):
    """Lessons are optional: every entry point is a silent no-op when off."""

    def setUp(self) -> None:
        self.service = TaskLessonService(logger=MagicMock())

    def test_reports_itself_disabled(self) -> None:
        self.assertFalse(self.service.enabled)

    def test_every_entry_point_is_a_no_op(self) -> None:
        self.service.capture_prompt_lesson_candidate('T1', 'a real lesson here')
        self.service.capture_candidate('cid', 'context')
        self.service.capture_task_lesson('T1', 'context')
        self.assertEqual(self.service.promote_candidates('task__T1__'), [])


class CaptureCandidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.lessons = MagicMock()
        self.service = TaskLessonService(
            lessons_service=self.lessons, logger=MagicMock(),
        )

    def test_stages_a_candidate_without_compacting(self) -> None:
        self.service.capture_candidate('cid', 'because X broke')
        _settle()

        self.lessons.extract_candidate_and_save.assert_called_once_with(
            'cid', 'because X broke',
        )
        self.lessons.compact.assert_not_called()

    def test_a_failing_extraction_never_escapes_the_worker(self) -> None:
        self.lessons.extract_candidate_and_save.side_effect = RuntimeError('llm down')

        self.service.capture_candidate('cid', 'context')
        _settle()  # no raise, no crash

        self.lessons.extract_candidate_and_save.assert_called_once()

    def test_a_prompt_is_staged_under_its_task_prefix(self) -> None:
        self.service.capture_prompt_lesson_candidate('T1', 'please fix the tabs')
        _settle()

        candidate_id = self.lessons.extract_candidate_and_save.call_args.args[0]
        self.assertTrue(candidate_id.startswith('task__T1__prompt__'))

    def test_a_trivial_prompt_spawns_nothing(self) -> None:
        # The wart this guards: every "continue" used to spend a throwaway
        # ``claude -p`` and leave a stray transcript in the operator's history.
        for trivial in ('continue', 'ok', '  CONTINUE  ', '👍', '   '):
            self.service.capture_prompt_lesson_candidate('T1', trivial)
        _settle()

        self.lessons.extract_candidate_and_save.assert_not_called()


class PromoteCandidatesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.lessons = MagicMock()
        self.logger = MagicMock()
        self.service = TaskLessonService(
            lessons_service=self.lessons, logger=self.logger,
        )

    def test_promotes_then_compacts(self) -> None:
        self.lessons.promote_candidates.return_value = ['task__T1__prompt__a']

        promoted = self.service.promote_candidates('task__T1__')

        self.assertEqual(promoted, ['task__T1__prompt__a'])
        self.lessons.compact.assert_called_once_with()

    def test_nothing_promoted_means_nothing_to_compact(self) -> None:
        self.lessons.promote_candidates.return_value = []

        self.assertEqual(self.service.promote_candidates('task__T1__'), [])
        self.lessons.compact.assert_not_called()

    def test_compact_false_leaves_compaction_to_the_caller(self) -> None:
        self.lessons.promote_candidates.return_value = ['a']

        self.service.promote_candidates('task__T1__', compact=False)

        self.lessons.compact.assert_not_called()

    def test_a_failure_is_logged_and_reported_as_nothing_promoted(self) -> None:
        self.lessons.promote_candidates.side_effect = RuntimeError('store gone')

        self.assertEqual(self.service.promote_candidates('task__T1__'), [])
        self.logger.exception.assert_called_once()


class CaptureTaskLessonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.lessons = MagicMock()
        self.service = TaskLessonService(
            lessons_service=self.lessons, logger=MagicMock(),
        )

    def test_promotes_the_tasks_candidates_then_mines_the_task(self) -> None:
        self.lessons.promote_candidates.return_value = ['task__T1__prompt__a']
        self.lessons.extract_and_save.return_value = '- a concrete rule'

        self.service.capture_task_lesson('T1', 'what publish did')
        _settle()

        self.lessons.promote_candidates.assert_called_once_with('task__T1__')
        self.lessons.extract_and_save.assert_called_once_with('T1', 'what publish did')
        # One compaction for the pair, not one per lesson.
        self.lessons.compact.assert_called_once_with()

    def test_no_lesson_and_no_candidates_means_no_compaction(self) -> None:
        self.lessons.promote_candidates.return_value = []
        self.lessons.extract_and_save.return_value = ''

        self.service.capture_task_lesson('T1', 'context')
        _settle()

        self.lessons.compact.assert_not_called()

    def test_a_failing_extraction_never_escapes_the_worker(self) -> None:
        self.lessons.promote_candidates.return_value = []
        self.lessons.extract_and_save.side_effect = RuntimeError('llm fail')

        self.service.capture_task_lesson('T1', 'context')
        _settle()  # no raise

        self.lessons.compact.assert_not_called()


if __name__ == '__main__':
    unittest.main()
