import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from kato_core_lib.data_layers.data_access.lessons_data_access import (
    LessonsDataAccess,
)
from kato_core_lib.data_layers.service.lessons_service import (
    FILED_MARKER,
    LessonsService,
)
from kato_core_lib.data_layers.service.task_lesson_service import (
    TaskLessonService,
    is_trivial_lesson_prompt,
)
from kato_core_lib.helpers.lesson_candidate_utils import (
    all_task_lesson_candidate_prefixes,
    comment_lesson_candidate_id,
    comment_lesson_candidate_prefix,
    task_lesson_candidate_id,
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

    def test_stages_a_candidate_without_filing_it(self) -> None:
        self.service.capture_candidate('cid', 'because X broke')
        _settle()

        self.lessons.extract_candidate_and_save.assert_called_once_with(
            'cid', 'because X broke',
        )
        self.lessons.file_pending.assert_not_called()

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

    def test_promotes_then_files(self) -> None:
        self.lessons.promote_candidates.return_value = ['task__T1__prompt__a']

        promoted = self.service.promote_candidates('task__T1__')
        _settle()

        self.assertEqual(promoted, ['task__T1__prompt__a'])
        self.lessons.file_pending.assert_called_once_with()

    def test_filing_does_not_hold_up_the_caller(self) -> None:
        # Filing is an AI run that takes minutes, and the caller is the
        # comment queue: it dispatches the next comment when this returns.
        self.lessons.promote_candidates.return_value = ['task__T1__prompt__a']
        release = threading.Event()
        self.lessons.file_pending.side_effect = lambda: release.wait(timeout=5)

        started = time.time()
        promoted = self.service.promote_candidates('task__T1__')
        elapsed = time.time() - started
        release.set()

        self.assertEqual(promoted, ['task__T1__prompt__a'])
        self.assertLess(elapsed, 1.0)

    def test_nothing_promoted_means_nothing_to_file(self) -> None:
        self.lessons.promote_candidates.return_value = []

        self.assertEqual(self.service.promote_candidates('task__T1__'), [])
        self.lessons.file_pending.assert_not_called()

    def test_file_false_leaves_filing_to_the_caller(self) -> None:
        self.lessons.promote_candidates.return_value = ['a']

        self.service.promote_candidates('task__T1__', file=False)

        self.lessons.file_pending.assert_not_called()

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
        # One write to the document for the pair, not one per lesson.
        self.lessons.file_pending.assert_called_once_with()

    def test_no_lesson_and_no_candidates_means_nothing_is_filed(self) -> None:
        self.lessons.promote_candidates.return_value = []
        self.lessons.extract_and_save.return_value = ''

        self.service.capture_task_lesson('T1', 'context')
        _settle()

        self.lessons.file_pending.assert_not_called()

    def test_a_failing_extraction_never_escapes_the_worker(self) -> None:
        self.lessons.promote_candidates.return_value = []
        self.lessons.extract_and_save.side_effect = RuntimeError('llm fail')

        self.service.capture_task_lesson('T1', 'context')
        _settle()  # no raise

        self.lessons.file_pending.assert_not_called()


class CandidateIdFormatTests(unittest.TestCase):
    """One home for the id format, shared by capture, promotion and release."""

    def test_both_families_carry_the_task_id_first(self) -> None:
        self.assertTrue(
            task_lesson_candidate_id('T1', 'prompt').startswith('task__T1__prompt__'),
        )
        self.assertTrue(
            comment_lesson_candidate_id('T1', 'c1').startswith('comment__T1__c1__'),
        )
        self.assertEqual(
            comment_lesson_candidate_prefix('T1', 'c1'), 'comment__T1__c1__',
        )

    def test_all_prefixes_cover_every_family_a_task_can_stage(self) -> None:
        prefixes = all_task_lesson_candidate_prefixes('T1')

        for candidate_id in (
            task_lesson_candidate_id('T1', 'prompt'),
            comment_lesson_candidate_id('T1', 'c1'),
        ):
            self.assertTrue(
                any(candidate_id.startswith(prefix) for prefix in prefixes),
                candidate_id,
            )
        # ...and none of them reaches a different task with a longer id.
        for other in (
            task_lesson_candidate_id('T10', 'prompt'),
            comment_lesson_candidate_id('T10', 'c1'),
        ):
            self.assertFalse(
                any(other.startswith(prefix) for prefix in prefixes), other,
            )


class ReleaseTaskCandidatesTests(unittest.TestCase):
    """Deleting a task settles its candidates — none is left behind."""

    def setUp(self) -> None:
        self.lessons = MagicMock()
        self.service = TaskLessonService(
            lessons_service=self.lessons, logger=MagicMock(),
        )

    def test_an_abandoned_task_discards_without_promoting(self) -> None:
        self.service.release_task_candidates('T1', validated=False)
        _settle()

        self.assertEqual(
            [call.args for call in self.lessons.discard_candidates.call_args_list],
            [('task__T1__',), ('comment__T1__',)],
        )
        self.lessons.promote_candidates.assert_not_called()
        self.lessons.file_pending.assert_not_called()

    def test_a_task_marked_done_promotes_and_files_once(self) -> None:
        self.lessons.promote_candidates.side_effect = (
            lambda prefix: [f'{prefix}x']
        )

        self.service.release_task_candidates('T1', validated=True)
        _settle()

        self.assertEqual(
            [call.args for call in self.lessons.promote_candidates.call_args_list],
            [('task__T1__',), ('comment__T1__',)],
        )
        self.lessons.discard_candidates.assert_not_called()
        # One write to the document for both families, not one per prefix.
        self.lessons.file_pending.assert_called_once_with()

    def test_marked_done_with_nothing_staged_writes_nothing(self) -> None:
        self.lessons.promote_candidates.return_value = []

        self.service.release_task_candidates('T1', validated=True)
        _settle()

        self.lessons.file_pending.assert_not_called()

    def test_a_failing_discard_never_reaches_the_delete_request(self) -> None:
        self.lessons.discard_candidates.side_effect = OSError('disk')

        self.service.release_task_candidates('T1', validated=False)  # no raise

    def test_is_a_no_op_without_a_lessons_service(self) -> None:
        disabled = TaskLessonService(logger=MagicMock())

        disabled.release_task_candidates('T1', validated=False)
        disabled.release_task_candidates('T1', validated=True)
        self.assertEqual(disabled.discard_candidates('task__T1__'), [])


class ReleaseTaskCandidatesOnDiskTests(unittest.TestCase):
    """The same thing against the real services and a real directory."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_dir = Path(self._tmp.name)
        self.dao = LessonsDataAccess(self.state_dir)
        self.filed: list[str] = []

        def editor(prompt: str) -> str:
            self.filed.append(prompt)
            return FILED_MARKER

        self.service = TaskLessonService(
            lessons_service=LessonsService(
                self.dao, lambda _prompt: '', document_editor=editor,
            ),
            logger=MagicMock(),
        )
        self.dao.write_candidate('task__T1__prompt__a', '- prompt rule')
        self.dao.write_candidate('comment__T1__c1__b', '- comment rule')
        self.dao.write_candidate('task__T2__prompt__c', '- another task')

    def test_abandoned_leaves_only_the_other_tasks_candidates(self) -> None:
        self.service.release_task_candidates('T1', validated=False)

        self.assertEqual(self.dao.list_candidate_ids(), ['task__T2__prompt__c'])
        self.assertEqual(self.dao.list_per_task_ids(), [])
        self.assertEqual(self.filed, [])

    def test_marked_done_hands_both_families_to_the_document_editor(self) -> None:
        self.service.release_task_candidates('T1', validated=True)
        deadline = time.time() + 5
        while not self.filed and time.time() < deadline:
            time.sleep(0.01)
        _settle()

        self.assertEqual(self.dao.list_candidate_ids(), ['task__T2__prompt__c'])
        # One editor run for the task, carrying both families and nothing else.
        self.assertEqual(len(self.filed), 1)
        self.assertIn('- prompt rule', self.filed[0])
        self.assertIn('- comment rule', self.filed[0])
        self.assertNotIn('- another task', self.filed[0])
        self.assertEqual(self.dao.list_per_task_ids(), [])


if __name__ == '__main__':
    unittest.main()
