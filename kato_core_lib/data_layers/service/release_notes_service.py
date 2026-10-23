"""Add a finished task to the release notes.

Orchestration only — every step is its own module:

* settings (on/off, where the file is)  — ``release_notes_settings_utils``
* what the task was                      — ``ReleaseNoteSources``
* where the entry goes in the document   — ``release_notes_document_utils``
* the locked, atomic file update         — ``release_notes_file_utils``
* the line itself                        — ``ReleaseNoteWriter``
* its pull-request links                 — ``ReleaseNoteLinks``
* anything that follows a write (the Google Doc copy) — ``after_write``

Written in TWO steps, so a note is never lost:

1. At once, while the task's workspace still exists (the forget flow deletes
   it right after): the facts are read and a dated line with the ticket title
   is written. Even if kato stops a second later, the task is in the notes.
2. In the background: the model's user-facing line and the pull-request
   links replace that line — but only if it is still exactly as written, so
   an operator's edit is never overwritten.

Never raises into the action that finished the task: the ticket is done
either way, and the notes are a record of it, not a condition for it.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import date
from pathlib import Path

from kato_core_lib.data_layers.data.release_note_source import ReleaseNoteSource
from kato_core_lib.helpers.logging_utils import configure_logger
from kato_core_lib.helpers.release_notes_document_utils import (
    add_entry,
    format_entry,
    replace_entry_line,
)
from kato_core_lib.helpers.release_notes_file_utils import update_release_notes
from kato_core_lib.helpers.release_notes_settings_utils import (
    path_problem,
    release_notes_enabled,
    release_notes_path,
    workspaces_root,
)


def run_in_background(work: Callable[[], None]) -> None:
    threading.Thread(target=work, name='release-notes', daemon=True).start()


class ReleaseNotesService(object):
    def __init__(
        self,
        *,
        sources,
        writer,
        links,
        after_write: Callable[[], None] | None = None,
        background: Callable[[Callable[[], None]], None] = run_in_background,
        today: Callable[[], str] = lambda: date.today().isoformat(),
        enabled: Callable[[], bool] = release_notes_enabled,
        path: Callable[[], Path] = release_notes_path,
        workspaces: Callable[[], Path] = workspaces_root,
        logger=None,
    ) -> None:
        self._sources = sources
        self._writer = writer
        self._links = links
        self._after_write = after_write
        self._background = background
        self._today = today
        self._enabled = enabled
        self._path = path
        self._workspaces = workspaces
        self.logger = logger or configure_logger('ReleaseNotesService')

    def task_done(self, task_id: str) -> None:
        """Record that ``task_id`` reached Done. Best effort; never raises."""
        task = str(task_id or '').strip()
        if not task:
            return
        try:
            self._record(task)
        except Exception:
            self.logger.exception('could not add task %s to the release notes', task)

    def _record(self, task_id: str) -> None:
        if not self._enabled():
            return
        path = Path(self._path())
        problem = path_problem(path, workspaces=self._workspaces())
        if problem:
            self.logger.warning('release notes not written for %s: %s', task_id, problem)
            return
        source = self._sources.read(task_id)
        done_on = self._today()
        first = format_entry(task_id=task_id, text=source.fallback, done_on=done_on)
        added = update_release_notes(
            path,
            lambda markdown: add_entry(
                markdown, task_id=task_id, repositories=source.repositories, line=first,
            ),
            logger=self.logger,
        )
        if not added:
            return  # already in the notes (or the write failed, which was logged)
        self.logger.info('added task %s to the release notes at %s', task_id, path)
        self._notify()
        self._background(lambda: self._improve(path, source, first, done_on))

    def _improve(self, path: Path, source: ReleaseNoteSource, first: str, done_on: str) -> None:
        try:
            better = format_entry(
                task_id=source.task_id,
                text=self._writer.write(source),
                done_on=done_on,
                links=self._links.links(source),
            )
            if better == first:
                return
            changed = update_release_notes(
                path,
                lambda markdown: replace_entry_line(
                    markdown, task_id=source.task_id, old=first, new=better,
                )[0],
                logger=self.logger,
            )
            if changed:
                self._notify()
        except Exception:
            self.logger.exception('could not finish the release note for task %s', source.task_id)

    def _notify(self) -> None:
        if self._after_write is None:
            return
        try:
            self._after_write()
        except Exception:
            self.logger.exception('release notes: a follow-up of the write failed')
