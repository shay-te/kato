"""Where review loops live on disk: ``<root>/<task_id>/<loop_id>/``.

Each loop folder holds ``state.json`` (rewritten after every step) and one
artifact per round and kind — the diff the reviewer was shown, the reviewer's
full reply, and the message posted to the chat — so an operator can see
exactly what each round saw and said.

The root is the host's choice and should sit OUTSIDE the task's own
repositories: nothing here may ever be committed, and a big diff artifact must
not be scanned or mounted as part of the task's workspace.

Paths are built only from validated ids and a fixed set of artifact kinds, so
an id coming in from a request can never point outside the root.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import threading
from enum import Enum
from pathlib import Path

from utils_core_lib.utils_core_lib.atomic_write import atomic_write_json

from review_loop_core_lib.review_loop_core_lib.data.state import ReviewLoopState

DEFAULT_KEEP_LOOPS = 5
_STATE_FILE = 'state.json'
_TASK_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')
_LOOP_ID = re.compile(r'^[0-9a-f]{32}$')


class ArtifactKind(str, Enum):
    DIFF = 'diff'        # what the reviewer was shown
    REVIEW = 'review'    # the reviewer's full reply
    PROMPT = 'prompt'    # the message posted into the chat
    RESPONSE = 'response'  # the fix turn's final reply (its decisions)
    SELF_CHECK = 'self_check'  # the chat's reply to self-check turn N (N = turn number)
    TESTS = 'tests'      # the chat's reply to the test run after round N


class ReviewLoopStore(object):
    """Load, save and prune loop folders; refuse writes for a forgotten task."""

    def __init__(
        self, root, *, keep_loops: int = DEFAULT_KEEP_LOOPS, logger=None,
    ) -> None:
        self._root = Path(root)
        self._keep_loops = max(1, int(keep_loops))
        self._logger = logger or logging.getLogger('ReviewLoopStore')
        # Tasks being forgotten: a loop thread still finishing must not write
        # their folder back into existence after it was deleted.
        self._closed: set[str] = set()
        self._lock = threading.Lock()

    # ----- ids -----

    @staticmethod
    def is_valid_task_id(task_id: str) -> bool:
        text = str(task_id or '')
        return bool(_TASK_ID.match(text)) and '..' not in text

    @staticmethod
    def is_valid_loop_id(loop_id: str) -> bool:
        return bool(_LOOP_ID.match(str(loop_id or '')))

    def _task_dir(self, task_id: str) -> Path:
        if not self.is_valid_task_id(task_id):
            raise ValueError(f'not a usable task id: {task_id!r}')
        return self._root / task_id

    def _loop_dir(self, task_id: str, loop_id: str) -> Path:
        if not self.is_valid_loop_id(loop_id):
            raise ValueError(f'not a usable loop id: {loop_id!r}')
        return self._task_dir(task_id) / loop_id

    # ----- lifecycle -----

    def open(self, task_id: str) -> None:
        """Allow writes for ``task_id`` again (a task re-added after a forget)."""
        with self._lock:
            self._closed.discard(task_id)

    def close(self, task_id: str) -> None:
        with self._lock:
            self._closed.add(task_id)

    def _is_closed(self, task_id: str) -> bool:
        with self._lock:
            return task_id in self._closed

    # ----- state -----

    def save(self, state: ReviewLoopState) -> bool:
        if self._is_closed(state.task_id):
            return False
        path = self._loop_dir(state.task_id, state.loop_id) / _STATE_FILE
        return atomic_write_json(
            path, state.to_dict(), logger=self._logger, label='review loop state',
        )

    def latest(self, task_id: str) -> ReviewLoopState | None:
        loops = self._loops(task_id)
        return loops[0] if loops else None

    def task_ids(self) -> list[str]:
        if not self._root.is_dir():
            return []
        return sorted(
            entry.name for entry in self._root.iterdir()
            if entry.is_dir() and self.is_valid_task_id(entry.name)
        )

    def latest_by_task(self) -> dict[str, ReviewLoopState]:
        found: dict[str, ReviewLoopState] = {}
        for task_id in self.task_ids():
            state = self.latest(task_id)
            if state is not None:
                found[task_id] = state
        return found

    def _loops(self, task_id: str) -> list[ReviewLoopState]:
        """Every readable loop for the task, newest first."""
        task_dir = self._task_dir(task_id)
        if not task_dir.is_dir():
            return []
        loops: list[ReviewLoopState] = []
        for entry in task_dir.iterdir():
            if not (entry.is_dir() and self.is_valid_loop_id(entry.name)):
                continue
            state = self._read_state(entry / _STATE_FILE)
            if state is not None:
                loops.append(state)
        loops.sort(key=lambda state: state.started_at, reverse=True)
        return loops

    def _read_state(self, path: Path) -> ReviewLoopState | None:
        try:
            return ReviewLoopState.from_dict(json.loads(path.read_text(encoding='utf-8')))
        except (OSError, ValueError, KeyError, TypeError):
            # A torn or foreign file is skipped, never fatal: one bad folder
            # must not hide every other loop of the task.
            return None

    def prune(self, task_id: str) -> int:
        """Keep the newest ``keep_loops`` loops of the task; returns how many went."""
        removed = 0
        for state in self._loops(task_id)[self._keep_loops:]:
            shutil.rmtree(self._loop_dir(task_id, state.loop_id), ignore_errors=True)
            removed += 1
        return removed

    def delete_task(self, task_id: str) -> None:
        shutil.rmtree(self._task_dir(task_id), ignore_errors=True)

    # ----- artifacts -----

    def write_artifact(
        self, state: ReviewLoopState, round_number: int, kind: ArtifactKind, text: str,
    ) -> bool:
        if self._is_closed(state.task_id):
            return False
        path = self._artifact_path(state.task_id, state.loop_id, round_number, kind)
        return atomic_write_json(
            path,
            {'round': int(round_number), 'kind': kind.value, 'text': str(text or '')},
            logger=self._logger,
            label=f'review loop {kind.value}',
        )

    def read_artifact(
        self, task_id: str, loop_id: str, round_number: int, kind: ArtifactKind,
    ) -> str | None:
        path = self._artifact_path(task_id, loop_id, round_number, kind)
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None
        return str(payload.get('text', '')) if isinstance(payload, dict) else None

    def _artifact_path(
        self, task_id: str, loop_id: str, round_number: int, kind: ArtifactKind,
    ) -> Path:
        number = int(round_number)
        if number < 1:
            raise ValueError(f'not a round number: {round_number!r}')
        return self._loop_dir(task_id, loop_id) / f'round-{number}-{ArtifactKind(kind).value}.json'
