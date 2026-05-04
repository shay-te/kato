"""Lesson capture, compaction, and system-prompt addendum.

Lifecycle:

  1. **Extract** — when a task is marked done (or a review-fix
     completes), kato calls :meth:`extract_and_save` with a short
     "what happened" context. The service asks Claude for ONE concrete
     rule that would have prevented a real mistake on this task.
     Junk is discarded — no extraction, no file. A real lesson lands
     in ``lessons/<task-id>.md``, overwriting any prior write for the
     same task.

  2. **Compact** — periodically (default >= 24h since last compact),
     the service merges every per-task pending lesson into the global
     ``lessons.md`` and drops duplicates / vague platitudes. The
     timestamp header records the merge time.

  3. **Inject** — :meth:`compose_addendum` returns the global file
     body for inclusion in the Claude system prompt on every spawn.
     The compact step rewrites this file in place; subsequent spawns
     pick up the new lessons without restarting kato.

The Claude calls are abstracted as ``llm_one_shot(prompt) -> str`` so
the service stays unit-testable without spawning subprocesses.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import Callable

from kato_core_lib.data_layers.data_access.lessons_data_access import (
    LessonsDataAccess,
    strip_timestamp_header,
)
from kato_core_lib.helpers.logging_utils import configure_logger


# Constrained extraction prompt. The "no junk" discipline lives here:
# unless the model can name a specific rule that would have prevented
# a concrete failure, it must output the literal NO_LESSON marker.
EXTRACTION_INSTRUCTIONS = (
    'You are reviewing a completed Kato task. Extract at most ONE '
    'concrete rule that would have prevented a real mistake on this '
    'task and would help on future tasks in the same codebase.\n'
    '\n'
    'The rule MUST:\n'
    ' - Name a specific function, file, pattern, library, or constraint.\n'
    ' - Be checkable (a future reader can tell whether it was followed).\n'
    ' - Tie back to a concrete thing that happened in this task context.\n'
    '\n'
    'The rule MUST NOT be:\n'
    ' - Vague ("write good code", "be careful with edge cases").\n'
    ' - Generic best-practice advice ("add tests", "use type hints").\n'
    ' - About the kato agent itself; only about the codebase under work.\n'
    '\n'
    'If no such rule applies, output exactly the literal string '
    '"NO_LESSON" and nothing else. Otherwise output the rule as a '
    'single line beginning with "- " and nothing else.'
)


COMPACT_INSTRUCTIONS = (
    'Merge the pending per-task lessons into the existing core lessons. '
    'Output ONLY the merged core-lessons section (no preamble, no '
    'commentary, no headers, no fences).\n'
    '\n'
    'Rules:\n'
    ' - Drop duplicates and near-duplicates; merge wording when two '
    'lines say the same thing.\n'
    ' - Drop any line that is vague or fluff (e.g. "write good code").\n'
    ' - Preserve every specific, checkable rule from the existing core '
    '   unless it is now contradicted or replaced by a pending lesson.\n'
    ' - One rule per line, each line prefixed with "- ".'
)


NO_LESSON_MARKER = 'NO_LESSON'


class LessonsService(object):
    """Per-task lesson capture + periodic global compaction."""

    def __init__(
        self,
        data_access: LessonsDataAccess,
        llm_one_shot: Callable[[str], str],
        *,
        compact_interval: timedelta = timedelta(hours=24),
        logger=None,
    ) -> None:
        self._data_access = data_access
        self._llm_one_shot = llm_one_shot
        self._compact_interval = compact_interval
        self._compact_lock = threading.Lock()
        self.logger = logger or configure_logger(self.__class__.__name__)

    @property
    def data_access(self) -> LessonsDataAccess:
        return self._data_access

    # ----- per-task capture -----

    def extract_and_save(self, task_id: str, task_context: str) -> str:
        """Extract a lesson from ``task_context`` and overwrite the per-task file.

        Returns the saved lesson, or the empty string if nothing useful
        was extracted (in which case any prior per-task file is removed
        so a re-run that produces no lesson doesn't leave stale text).

        ``task_context`` is whatever bag of text best summarises the
        task — the operator's prompt, the diff, error messages, the
        review comment that drove the fix. The caller assembles it.
        """
        normalized_task_id = str(task_id or '').strip()
        if not normalized_task_id:
            self.logger.warning('extract_and_save called with empty task id; skipping')
            return ''
        prompt = self._build_extraction_prompt(normalized_task_id, task_context)
        try:
            response = self._llm_one_shot(prompt)
        except Exception:
            self.logger.exception(
                'lesson extraction failed for task %s; per-task file untouched',
                normalized_task_id,
            )
            return ''
        lesson = self._parse_extraction_response(response)
        if not lesson:
            self.logger.info(
                'no lesson extracted for task %s; clearing any prior per-task file',
                normalized_task_id,
            )
            self._data_access.delete_per_task(normalized_task_id)
            return ''
        self._data_access.write_per_task(normalized_task_id, lesson)
        self.logger.info('saved lesson for task %s', normalized_task_id)
        return lesson

    # ----- compact -----

    def should_compact(self, *, now: datetime | None = None) -> bool:
        """True iff a compact run is due.

        A compact is due when the global file has never been compacted
        AND there is pending per-task work to merge, OR when the last
        compaction happened at least ``compact_interval`` ago.
        """
        last = self._data_access.last_compacted_at()
        if last is None:
            return bool(self._data_access.list_per_task_ids())
        current = now or datetime.now(timezone.utc)
        return (current - last) >= self._compact_interval

    def compact(self) -> bool:
        """Merge pending per-task lessons into the global file.

        Returns True if a merge actually happened. Concurrent compact
        calls are a no-op for the second caller — the lock is acquired
        non-blockingly so the background thread doesn't queue behind a
        manually-triggered compact (or vice versa).
        """
        if not self._compact_lock.acquire(blocking=False):
            self.logger.info('compact already in progress; skipping')
            return False
        try:
            return self._compact_locked()
        finally:
            self._compact_lock.release()

    def _compact_locked(self) -> bool:
        pending = self._data_access.read_all_per_task()
        existing_core = self._data_access.read_global_body().strip()
        if not pending and not existing_core:
            self.logger.info('compact: no pending or existing lessons; nothing to do')
            return False
        if not pending:
            # No pending — refresh the timestamp so the 24h gate slides
            # forward, but don't burn an LLM call to re-merge what's
            # already there.
            self._data_access.write_global(existing_core + '\n')
            return True
        prompt = self._build_compact_prompt(existing_core, pending)
        try:
            merged = self._llm_one_shot(prompt)
        except Exception:
            self.logger.exception(
                'compact LLM call failed; leaving lesson files untouched',
            )
            return False
        cleaned = (merged or '').strip()
        if not cleaned:
            self.logger.warning(
                'compact returned empty result; leaving lesson files untouched',
            )
            return False
        if not self._data_access.write_global(cleaned + '\n'):
            return False
        for task_id in list(pending.keys()):
            self._data_access.delete_per_task(task_id)
        self.logger.info(
            'compacted %d pending lesson(s) into global', len(pending),
        )
        return True

    # ----- system prompt addendum -----

    def compose_addendum(self) -> str:
        """Return the global lessons body for system-prompt injection.

        Strips the timestamp header and returns the empty string when
        there are no lessons yet — callers can use the empty result to
        skip injection entirely.
        """
        return self._data_access.read_global_body().strip()

    # ----- internals -----

    def _build_extraction_prompt(self, task_id: str, task_context: str) -> str:
        return (
            f'{EXTRACTION_INSTRUCTIONS}\n'
            f'\n'
            f'Task id: {task_id}\n'
            f'\n'
            f'Task context:\n'
            f'{task_context}\n'
        )

    def _build_compact_prompt(
        self,
        existing_core: str,
        pending: dict[str, str],
    ) -> str:
        pending_section = '\n\n'.join(
            f'## Pending: {task_id}\n{strip_timestamp_header(content).strip()}'
            for task_id, content in sorted(pending.items())
        )
        return (
            f'{COMPACT_INSTRUCTIONS}\n'
            f'\n'
            f'## Existing core lessons\n'
            f'{existing_core or "(none)"}\n'
            f'\n'
            f'{pending_section}\n'
        )

    @staticmethod
    def _parse_extraction_response(response: str) -> str:
        """Pick the first bullet line out of ``response``.

        The extraction prompt asks Claude to either output ``NO_LESSON``
        or a single ``- `` bullet. We're permissive on the bullet — any
        first non-empty line that starts with ``- `` and has substance
        after the dash counts. Anything else returns ``''`` so junk
        responses don't leak into the lessons file.
        """
        text = (response or '').strip()
        if not text or text == NO_LESSON_MARKER:
            return ''
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith('- ') and len(stripped) > 2:
                return stripped
        return ''
