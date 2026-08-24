"""Lesson capture: candidates in, saved lessons out.

Kato mines lessons from what actually happened — an operator's chat prompt, a
diff comment they had to write, a task that just published. Every one of those
is a background, best-effort job: a lesson is a nice-to-have, so nothing here
is ever allowed to fail a foreground operation or crash a worker thread.

Three subsystems used to reach for this through host callbacks
(``kick_lesson_candidate_extraction``, ``promote_lesson_candidates``) and a
raw ``lessons_service`` handle. They take this object instead — one
collaborator with a named API, rather than three loose functions.
"""

from __future__ import annotations

import threading

from kato_core_lib.helpers.late_binding import provider_for
from kato_core_lib.helpers.logging_utils import configure_logger
from kato_core_lib.helpers.lesson_candidate_utils import (
    task_lesson_candidate_id,
    task_lesson_candidate_prefix,
)

# Chat prompts that carry no signal. Mining these would spend a throwaway
# ``claude -p`` (and leave a stray transcript) per "ok".
_TRIVIAL_LESSON_PROMPTS = frozenset({
    'continue', 'continue from where you left off',
    'please continue', 'please continue from where you left off',
    'go', 'go on', 'go ahead', 'keep going', 'carry on', 'proceed', 'next',
    'yes', 'yeah', 'yep', 'ok', 'okay', 'sure', 'k', 'y',
    'no', 'nope', 'n', 'stop',
    'thanks', 'thank you', 'ty',
})


def is_trivial_lesson_prompt(text: str) -> bool:
    """True for a no-signal chat prompt that shouldn't trigger lesson mining."""
    normalized = ' '.join(str(text or '').split()).lower().strip(' .!?,…')
    if not normalized:
        return True
    if normalized in _TRIVIAL_LESSON_PROMPTS:
        return True
    # Ultra-short tokens (emoji, "k", "👍") never encode a lesson.
    return len(normalized) < 4


class TaskLessonService(object):
    """Stage, promote, and extract lessons for a task — always in the background."""

    def __init__(self, *, lessons_service=None, logger=None) -> None:
        self._get_lessons_service = provider_for(lessons_service)
        self._logger_getter = provider_for(
            logger if logger is not None else configure_logger('TaskLessonService'),
        )

    @property
    def logger(self):
        """The host's CURRENT logger — resolved per call, never captured."""
        return self._logger_getter()

    @property
    def _lessons_service(self):
        return self._get_lessons_service()

    @property
    def enabled(self) -> bool:
        """False when no lessons service is configured — every call is a no-op."""
        return self._lessons_service is not None

    def capture_prompt_lesson_candidate(self, task_id: str, prompt: str) -> None:
        """Stage a candidate lesson from an operator chat prompt."""
        text = str(prompt or '').strip()
        if not text or is_trivial_lesson_prompt(text):
            return
        self.capture_candidate(
            task_lesson_candidate_id(task_id, 'prompt'),
            f'Operator chat prompt for task {task_id}:\n{text}',
        )

    def capture_candidate(self, candidate_id: str, source_context: str) -> None:
        """Extract one candidate from ``source_context`` and stage it.

        Staged, not promoted: a candidate only becomes a lesson once the work
        it came from is addressed (see :meth:`promote_candidates`).
        """
        if not self.enabled:
            return
        self._in_background(f'lesson-candidate-{candidate_id}', lambda: (
            self._lessons_service.extract_candidate_and_save(
                candidate_id, source_context,
            )
        ))

    def promote_candidates(self, prefix: str, *, compact: bool = True) -> list[str]:
        """Promote every candidate staged under ``prefix`` to a real lesson.

        Runs inline (the caller is already off the request path) and returns
        the promoted ids. ``compact=False`` leaves compaction to a caller that
        is about to write more lessons anyway.
        """
        if not self.enabled:
            return []
        try:
            promoted = self._lessons_service.promote_candidates(prefix)
            if promoted and compact:
                self._lessons_service.compact()
            return promoted
        except Exception:
            self.logger.exception('failed to promote lesson candidates')
            return []

    def capture_task_lesson(self, task_id: str, task_context: str) -> None:
        """Wrap up a finished task: promote its candidates, then mine the task itself.

        One background pass, one compaction at the end — the candidates staged
        during the task and the lesson extracted from its outcome land
        together.
        """
        if not self.enabled:
            return

        def _run() -> None:
            promoted = self.promote_candidates(
                task_lesson_candidate_prefix(task_id), compact=False,
            )
            lesson = self._lessons_service.extract_and_save(task_id, task_context)
            if lesson or promoted:
                self._lessons_service.compact()

        self._in_background(f'lesson-extract-{task_id}', _run)

    def _in_background(self, name: str, work) -> None:
        """Run ``work`` on a daemon thread, swallowing anything it raises.

        A lesson is best-effort: the services below already log, and a worker
        that dies must never surface as a failed push or a failed comment.
        """
        def _guarded() -> None:
            try:
                work()
            except Exception:
                pass

        threading.Thread(
            target=_guarded, name=f'kato-{name}', daemon=True,
        ).start()
