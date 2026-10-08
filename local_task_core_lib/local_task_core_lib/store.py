"""Every local task, in one JSON file.

Each change is a read-modify-write held under a cross-process file lock and
written atomically, so two processes (or threads) adding tasks at the same
moment can neither lose one nor hand out the same id twice.

Ids are ``<prefix>-<n>`` from a counter that only ever moves forward: deleting
task 3 never makes a later task "3" again, because a branch or a pull request
named after the old one may still be out there.

A file that cannot be parsed is never overwritten. Writing an empty list over
it would silently delete every task in it; the store raises instead and leaves
the file for a person to look at.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from local_task_core_lib.local_task_core_lib.data.local_task import (
    LocalComment,
    LocalTask,
    LocalTaskState,
)
from utils_core_lib.utils_core_lib.atomic_write import atomic_write_json
from utils_core_lib.utils_core_lib.file_lock import exclusive_file_lock

DEFAULT_ID_PREFIX = 'LOCAL'


class LocalTaskStoreError(RuntimeError):
    """The store file exists but cannot be read; nothing was changed."""


class LocalTaskNotFoundError(KeyError):
    """No local task has that id."""


class LocalTaskStore(object):

    def __init__(
        self,
        path: str | Path,
        *,
        id_prefix: str = DEFAULT_ID_PREFIX,
        clock: Callable[[], float] = time.time,
    ) -> None:
        prefix = str(id_prefix or '').strip().upper()
        if not re.fullmatch(r'[A-Z][A-Z0-9]*', prefix):
            raise ValueError(f'id prefix must be letters and digits, got {id_prefix!r}')
        self._path = Path(path)
        self._prefix = prefix
        self._id_pattern = re.compile(rf'{re.escape(prefix)}-(\d+)', re.IGNORECASE)
        self._clock = clock

    @property
    def path(self) -> Path:
        return self._path

    @property
    def id_prefix(self) -> str:
        return self._prefix

    def owns(self, task_id: object) -> bool:
        """Whether ``task_id`` names a task of this store (it may be deleted)."""
        return self._id_pattern.fullmatch(str(task_id or '').strip()) is not None

    # ----- reads -----

    def get(self, task_id: object) -> LocalTask | None:
        wanted = str(task_id or '').strip().lower()
        for task in self._read()[1]:
            if task.id.lower() == wanted:
                return task
        return None

    def list(self, states: Iterable[LocalTaskState] | None = None) -> list[LocalTask]:
        """Tasks in the order they were created; only ``states`` when given."""
        tasks = self._read()[1]
        if states is None:
            return tasks
        wanted = set(states)
        return [task for task in tasks if task.state in wanted]

    # ----- writes -----

    def create(
        self,
        summary: str,
        description: str = '',
        *,
        tags: Iterable[str] = (),
        state: LocalTaskState = LocalTaskState.OPEN,
    ) -> LocalTask:
        summary = str(summary or '').strip()
        if not summary:
            raise ValueError('a task needs a summary')
        with exclusive_file_lock(self._path):
            next_id, tasks = self._read()
            now = self._clock()
            task = LocalTask(
                id=f'{self._prefix}-{next_id}',
                summary=summary,
                description=str(description or '').strip(),
                tags=_unique(tags),
                state=LocalTaskState(state),
                created_at=now,
                updated_at=now,
            )
            tasks.append(task)
            self._write(next_id + 1, tasks)
        return task

    def set_state(self, task_id: object, state: LocalTaskState) -> LocalTask:
        def change(task: LocalTask) -> None:
            task.state = LocalTaskState(state)
        return self._update(task_id, change)

    def add_comment(
        self, task_id: object, body: str, *, author: str = '', author_id: str = '',
    ) -> LocalTask:
        text = str(body or '').strip()
        if not text:
            raise ValueError('a comment needs a body')

        def change(task: LocalTask) -> None:
            task.comments.append(LocalComment(
                body=text, author=author, author_id=author_id, created_at=self._clock(),
            ))
        return self._update(task_id, change)

    def add_tag(self, task_id: object, tag: str) -> LocalTask:
        def change(task: LocalTask) -> None:
            task.tags = _unique([*task.tags, tag])
        return self._update(task_id, change)

    def remove_tag(self, task_id: object, tag: str) -> LocalTask:
        wanted = str(tag or '').strip().lower()

        def change(task: LocalTask) -> None:
            task.tags = [existing for existing in task.tags if existing.lower() != wanted]
        return self._update(task_id, change)

    def delete(self, task_id: object) -> bool:
        wanted = str(task_id or '').strip().lower()
        with exclusive_file_lock(self._path):
            next_id, tasks = self._read()
            kept = [task for task in tasks if task.id.lower() != wanted]
            if len(kept) == len(tasks):
                return False
            self._write(next_id, kept)
        return True

    # ----- internals -----

    def _update(self, task_id: object, change: Callable[[LocalTask], None]) -> LocalTask:
        wanted = str(task_id or '').strip().lower()
        with exclusive_file_lock(self._path):
            next_id, tasks = self._read()
            for task in tasks:
                if task.id.lower() == wanted:
                    change(task)
                    task.updated_at = self._clock()
                    self._write(next_id, tasks)
                    return task
        raise LocalTaskNotFoundError(str(task_id))

    def _read(self) -> tuple[int, list[LocalTask]]:
        """``(next id number, tasks)``; a missing file is an empty store."""
        try:
            text = self._path.read_text(encoding='utf-8')
        except FileNotFoundError:
            return 1, []
        except OSError as exc:
            raise LocalTaskStoreError(f'cannot read {self._path}: {exc}') from exc
        try:
            payload = json.loads(text)
            tasks = [LocalTask.from_dict(item) for item in payload['tasks']]
            stored_next = int(payload.get('next_id') or 1)
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise LocalTaskStoreError(
                f'{self._path} is not a readable task list ({exc}); it was left untouched',
            ) from exc
        # Never below a number already handed out, even if the counter was
        # edited by hand.
        highest = max((self._number(task.id) for task in tasks), default=0)
        return max(stored_next, highest + 1), tasks

    def _number(self, task_id: str) -> int:
        match = self._id_pattern.fullmatch(task_id)
        return int(match.group(1)) if match else 0

    def _write(self, next_id: int, tasks: list[LocalTask]) -> None:
        payload: dict[str, Any] = {
            'next_id': next_id,
            'tasks': [task.to_dict() for task in tasks],
        }
        atomic_write_json(self._path, payload, raise_on_error=True, trailing_newline=True)


def _unique(tags: Iterable[str]) -> list[str]:
    """Tags in order, blanks dropped, case-insensitive duplicates removed."""
    seen: set[str] = set()
    kept: list[str] = []
    for tag in tags:
        text = str(tag or '').strip()
        if text and text.lower() not in seen:
            seen.add(text.lower())
            kept.append(text)
    return kept
