"""File I/O for the lessons subsystem.

File layout under ``state_dir``:

    state_dir/
      lessons.md                  <- THE lessons document: the one file the
                                     agent reads at the start of every task
                                     and edits at the end of one. Every write
                                     to it is an AI edit; this layer only
                                     replaces it wholesale for a restore or
                                     the one-time adoption of a legacy file.
      lessons/
        <id>.md                   <- validated lessons waiting to be filed
                                     into the document. Deleted once they are.
      lesson-candidates/
        <source-id>.md            <- untrusted lessons extracted early
                                     from prompts/comments. Promoted to
                                     lessons/ only after validation.

This data-access layer is policy-free: it does not call any LLM and does not
decide what counts as a lesson. Those decisions live in ``LessonsService``.
"""

from __future__ import annotations

import re
from pathlib import Path

from kato_core_lib.helpers.atomic_text_utils import atomic_write_text
from kato_core_lib.helpers.lessons_path_utils import (
    LESSON_CANDIDATES_DIRNAME,
    LESSONS_PER_TASK_DIRNAME,
)
from kato_core_lib.helpers.logging_utils import configure_logger


# The first line the retired compaction step used to stamp on the file. Nothing
# writes it any more; it is still recognised so a file from before reads clean.
_TIMESTAMP_PATTERN = re.compile(
    r'^<!--\s*last_compacted:\s*([0-9TZ:.\-+]+)\s*-->'
)

class LessonsDataAccess(object):
    """Read and write per-task and global lesson files."""

    def __init__(self, state_dir: Path) -> None:
        self._state_dir = Path(state_dir)
        self._global_path = self._state_dir / 'lessons.md'
        self._adopted_marker_path = self._state_dir / 'lessons.adopted'
        self._per_task_dir = self._state_dir / LESSONS_PER_TASK_DIRNAME
        self._candidate_dir = self._state_dir / LESSON_CANDIDATES_DIRNAME
        self.logger = configure_logger(self.__class__.__name__)

    @property
    def state_dir(self) -> Path:
        return self._state_dir

    @property
    def global_path(self) -> Path:
        return self._global_path

    # ----- global lessons file -----

    def read_global(self) -> str:
        """Return the raw global file contents, or empty string if absent."""
        if not self._global_path.is_file():
            return ''
        try:
            return self._global_path.read_text(encoding='utf-8')
        except OSError:
            self.logger.exception('failed to read global lessons at %s', self._global_path)
            return ''

    def read_global_body(self) -> str:
        """Return the lessons document without a legacy timestamp header."""
        return strip_timestamp_header(self.read_global())

    def write_global(self, body: str) -> bool:
        """Replace the lessons document with ``body``, atomically."""
        composed = body if body.endswith('\n') else body + '\n'
        return atomic_write_text(
            self._global_path,
            composed,
            logger=self.logger,
            label='lessons document',
        )

    def backup_global(self, suffix: str) -> Path | None:
        """Copy the lessons document aside as ``lessons.md.<suffix>``.

        ``None`` when there is nothing to keep or the copy failed. Taken
        before the one write that replaces the file wholesale, so what was
        there is never the price of a migration.
        """
        current = self.read_global()
        if not current.strip():
            return None
        target = self._global_path.with_name(f'{self._global_path.name}.{suffix}')
        if not atomic_write_text(
            target, current, logger=self.logger, label='lessons backup',
        ):
            return None
        return target

    # ----- one-time adoption of a legacy document -----

    def adopted_legacy_path(self) -> str:
        """The legacy document already merged into the lessons file, or ''.

        Recorded in a sidecar rather than inside ``lessons.md`` on purpose:
        the agent edits that file, and a marker it tidied away would have the
        whole legacy document merged in a second time.
        """
        try:
            return self._adopted_marker_path.read_text(encoding='utf-8').strip()
        except OSError:
            return ''

    def mark_legacy_adopted(self, legacy_path: str) -> bool:
        return atomic_write_text(
            self._adopted_marker_path,
            f'{legacy_path}\n',
            logger=self.logger,
            label='lessons adoption marker',
        )

    # ----- per-task lessons -----

    def read_per_task(self, task_id: str) -> str | None:
        """Return per-task lesson content, or None if absent."""
        path = self._per_task_path(task_id)
        if path is None or not path.is_file():
            return None
        try:
            return path.read_text(encoding='utf-8')
        except OSError:
            self.logger.exception(
                'failed to read per-task lesson for %s at %s', task_id, path,
            )
            return None

    def write_per_task(self, task_id: str, content: str) -> bool:
        """Overwrite the per-task lesson file. Idempotent — same task
        marked done repeatedly replaces the previous content."""
        path = self._per_task_path(task_id)
        if path is None:
            self.logger.warning(
                'rejected per-task lesson write for invalid task id %r', task_id,
            )
            return False
        body = content if content.endswith('\n') else content + '\n'
        return atomic_write_text(
            path,
            body,
            logger=self.logger,
            label=f'per-task lesson {task_id}',
        )

    def delete_per_task(self, task_id: str) -> None:
        """Remove the per-task lesson file. No-op when absent."""
        path = self._per_task_path(task_id)
        if path is None or not path.is_file():
            return
        try:
            path.unlink()
        except OSError:
            self.logger.exception(
                'failed to delete per-task lesson at %s', path,
            )

    def list_per_task_ids(self) -> list[str]:
        """Return sorted task ids that currently have pending lesson files."""
        if not self._per_task_dir.is_dir():
            return []
        ids = []
        for entry in sorted(self._per_task_dir.iterdir()):
            if entry.is_file() and entry.suffix == '.md':
                ids.append(entry.stem)
        return ids

    def read_all_per_task(self) -> dict[str, str]:
        """Return ``{task_id: content}`` for every pending per-task file."""
        out: dict[str, str] = {}
        for task_id in self.list_per_task_ids():
            content = self.read_per_task(task_id)
            if content is not None:
                out[task_id] = content
        return out

    # ----- candidate lessons -----

    def read_candidate(self, candidate_id: str) -> str | None:
        """Return candidate lesson content, or None if absent."""
        path = self._candidate_path(candidate_id)
        if path is None or not path.is_file():
            return None
        try:
            return path.read_text(encoding='utf-8')
        except OSError:
            self.logger.exception(
                'failed to read candidate lesson for %s at %s',
                candidate_id, path,
            )
            return None

    def write_candidate(self, candidate_id: str, content: str) -> bool:
        """Overwrite a candidate lesson file."""
        path = self._candidate_path(candidate_id)
        if path is None:
            self.logger.warning(
                'rejected candidate lesson write for invalid id %r',
                candidate_id,
            )
            return False
        body = content if content.endswith('\n') else content + '\n'
        return atomic_write_text(
            path,
            body,
            logger=self.logger,
            label=f'candidate lesson {candidate_id}',
        )

    def delete_candidate(self, candidate_id: str) -> None:
        """Remove a candidate lesson file. No-op when absent."""
        path = self._candidate_path(candidate_id)
        if path is None or not path.is_file():
            return
        try:
            path.unlink()
        except OSError:
            self.logger.exception(
                'failed to delete candidate lesson at %s', path,
            )

    def list_candidate_ids(self, prefix: str = '') -> list[str]:
        """Return sorted candidate ids, optionally filtered by prefix."""
        if not self._candidate_dir.is_dir():
            return []
        normalized_prefix = str(prefix or '').strip()
        ids = []
        for entry in sorted(self._candidate_dir.iterdir()):
            if not entry.is_file() or entry.suffix != '.md':
                continue
            candidate_id = entry.stem
            if normalized_prefix and not candidate_id.startswith(normalized_prefix):
                continue
            ids.append(candidate_id)
        return ids

    # ----- internals -----

    def _per_task_path(self, task_id: str) -> Path | None:
        normalized = self._normalize_task_id(task_id)
        if not normalized:
            return None
        return self._per_task_dir / f'{normalized}.md'

    def _candidate_path(self, candidate_id: str) -> Path | None:
        normalized = self._normalize_task_id(candidate_id)
        if not normalized:
            return None
        return self._candidate_dir / f'{normalized}.md'

    @staticmethod
    def _normalize_task_id(task_id: str) -> str:
        # Reject characters and whole-string forms that would let a
        # caller escape the per-task directory or write outside the
        # state dir. Real task ids look like ``PROJ-123`` — none of
        # these are legitimate.
        if task_id is None:
            return ''
        normalized = str(task_id).strip()
        if not normalized:
            return ''
        if normalized in {'.', '..'}:
            return ''
        for forbidden in ('/', '\\', '..', '\x00'):
            if forbidden in normalized:
                return ''
        return normalized


def strip_timestamp_header(text: str) -> str:
    """Remove the leading ``<!-- last_compacted: ... -->`` line if present."""
    if not text:
        return ''
    lines = text.splitlines()
    if lines and _TIMESTAMP_PATTERN.match(lines[0]):
        return '\n'.join(lines[1:]).lstrip('\n')
    return text
