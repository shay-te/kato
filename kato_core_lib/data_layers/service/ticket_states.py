"""Each task's state in its ticket system — YouTrack, Jira, or kato's own local
tasks — for the tasks menu's status filter ("add a dropdown to select
YouTrack/Jira statuses, and all, to filter what tasks to show, so we know where
they are", 2026-11-18).

ONE batch read for every task kato has a workspace for (``get_issues_status``,
the call the plan board uses), at most once a minute, and NEVER while the tab
list is answered: that list is polled every five seconds, and a tracker call on
every poll is the 429 the review scan already had to learn to avoid.
``states()`` answers what was last read and, when that is a minute old, starts
one read in the background.

Local tasks are read from their own store. A tracker that cannot answer
(GitHub / GitLab / Bitbucket issues, no tracker at all) leaves its tasks with no
state; the menu shows them under "No status". A read that fails keeps the last
answer and is said once.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from kato_core_lib.helpers.late_binding import provider_for
from kato_core_lib.helpers.lessons_path_utils import is_reserved_workspace_dirname
from kato_core_lib.helpers.logging_utils import configure_logger
from kato_core_lib.helpers.said_once_utils import SaidOnce

REFRESH_SECONDS = 60
# Ids asked for in one tracker call.
BATCH_SIZE = 50
_READ_FAILED = 'read-failed'


def _in_thread(work: Callable[[], None]) -> None:
    threading.Thread(target=work, name='ticket-states', daemon=True).start()


class TicketStates(object):
    def __init__(
        self,
        *,
        task_service,
        task_state_service,
        workspace_manager,
        clock: Callable[[], float] = time.time,
        refresh_seconds: float = REFRESH_SECONDS,
        run_in_background: Callable[[Callable[[], None]], None] = _in_thread,
        logger=None,
    ) -> None:
        self._get_task_service = provider_for(task_service)
        self._get_task_state_service = provider_for(task_state_service)
        self._get_workspace_manager = provider_for(workspace_manager)
        self._clock = clock
        self._refresh_seconds = refresh_seconds
        self._background = run_in_background
        self._get_logger = provider_for(logger if logger is not None else configure_logger('TicketStates'))
        self._lock = threading.Lock()
        self._states: dict[str, str] = {}
        self._read_at: float | None = None
        self._reading = False
        self._said = SaidOnce()

    def states(self) -> dict[str, str]:
        """``{task id (lower case): state}`` as last read. Never waits for a
        tracker: a reading a minute old starts one new read in the background."""
        with self._lock:
            due = not self._reading and (
                self._read_at is None or self._clock() - self._read_at >= self._refresh_seconds
            )
            if due:
                self._reading = True
            current = dict(self._states)
        if due:
            self._background(self._read_in_background)
        return current

    def refresh(self) -> None:
        """Read every task's state now (one tracker call per ``BATCH_SIZE`` ids)."""
        task_ids = self._task_ids()
        task_service = self._get_task_service()
        is_local = getattr(task_service, 'is_local', None)
        local = [task_id for task_id in task_ids if callable(is_local) and is_local(task_id)]
        tracked = [task_id for task_id in task_ids if task_id not in local]
        states = {task_id.lower(): state for task_id, state in self._local_states(task_service, local).items()}
        states.update(self._tracker_states(task_service, tracked))
        with self._lock:
            self._states = states
            self._read_at = self._clock()

    def _read_in_background(self) -> None:
        try:
            self.refresh()
        except Exception:
            self._get_logger().exception('could not read the tasks\' ticket states')
            with self._lock:
                self._read_at = self._clock()  # wait the minute before trying again
        finally:
            with self._lock:
                self._reading = False

    def _task_ids(self) -> list[str]:
        manager = self._get_workspace_manager()
        if manager is None:
            return []
        return [
            str(record.task_id).strip() for record in manager.list_workspaces()
            if str(getattr(record, 'task_id', '') or '').strip()
            and not is_reserved_workspace_dirname(record.task_id)
        ]

    @staticmethod
    def _local_states(task_service, task_ids: list[str]) -> dict[str, str]:
        states = {}
        for task_id in task_ids:
            task = task_service.get_task(task_id)
            state = str(getattr(task, 'state', '') or '').strip() if task is not None else ''
            if state:
                states[task_id] = state
        return states

    def _tracker_states(self, task_service, task_ids: list[str]) -> dict[str, str]:
        """The tracker's states — or, when it cannot be read, the last ones."""
        tracker = task_service.subtask_tracker() if task_ids else None
        if tracker is None:
            return {}
        field = self._get_task_state_service().state_names().get('field', '')
        states: dict[str, str] = {}
        try:
            for start in range(0, len(task_ids), BATCH_SIZE):
                batch = task_ids[start:start + BATCH_SIZE]
                for task_id, status in tracker.get_issues_status(batch, field).items():
                    state = str(getattr(status, 'state', '') or '').strip()
                    if getattr(status, 'exists', True) and state:
                        states[str(task_id).lower()] = state
        except Exception as exc:
            if self._said.say(_READ_FAILED, str(exc)):
                self._get_logger().warning('could not read the tickets\' states (kept the last ones): %s', exc)
            with self._lock:
                return {task_id.lower(): self._states[task_id.lower()]
                        for task_id in task_ids if task_id.lower() in self._states}
        if self._said.ended(_READ_FAILED):
            self._get_logger().info('the tickets\' states can be read again')
        return states

