"""Where a plan's sub-tasks stand, read from the tracker — what every kato sees.

One read for all of a plan's sub-tasks (state, assignee, creation time), kept
for a minute so the queue's five-second tick does not hammer the tracker. The
kato that runs a sub-task adds what only it knows: its workspace broke.

Local tasks (``LOCAL-n``) are answered by the local adapter: ``RoutedTaskProgress``
sends each id to the one that owns it.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from datetime import datetime

from plan_queue_core_lib.plan_queue_core_lib.ports import TaskObservation, TaskPhase
from workspace_core_lib.workspace_core_lib.data_layers.data.workspace_record import (
    WORKSPACE_STATUS_ERRORED,
    WORKSPACE_STATUS_PROVISIONING,
)

from kato_core_lib.helpers.late_binding import provider_for

REFRESH_SECONDS = 60


class TrackerTaskProgress(object):
    def __init__(
        self, *, task_service, task_state_service, workspace_manager,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._get_task_service = provider_for(task_service)
        self._get_task_state_service = provider_for(task_state_service)
        self._get_workspace_manager = provider_for(workspace_manager)
        self._clock = clock
        self._seen: dict[str, tuple[float, object]] = {}

    def observe(self, task_ids: Sequence[str]) -> dict[str, TaskObservation]:
        tracker = self._get_task_service().subtask_tracker()
        if tracker is None or not task_ids:
            return {}  # unknown: the queue keeps every step where it is
        names = self._get_task_state_service().state_names()
        now = self._clock()
        stale = [task_id for task_id in task_ids if now - self._seen.get(task_id, (-1e18, None))[0] >= REFRESH_SECONDS]
        if stale:
            for task_id, status in tracker.get_issues_status(stale, names['field']).items():
                self._seen[task_id] = (now, status)
        workspaces = self._get_workspace_manager()
        return {
            task_id: _observation(self._seen[task_id][1], names, _record(workspaces, task_id))
            for task_id in task_ids if task_id in self._seen
        }


class RoutedTaskProgress(object):
    """Each task to the adapter that owns it: local tasks to the local store,
    every other task to the tracker."""

    def __init__(self, *, local, tracker, is_local: Callable[[str], bool]) -> None:
        self._local = local
        self._tracker = tracker
        self._is_local = is_local

    def observe(self, task_ids: Sequence[str]) -> dict[str, TaskObservation]:
        local = [task_id for task_id in task_ids if self._is_local(task_id)]
        tracked = [task_id for task_id in task_ids if not self._is_local(task_id)]
        found = dict(self._local.observe(local)) if local else {}
        if tracked:
            found.update(self._tracker.observe(tracked))
        return found


def _observation(status, names: dict[str, str], record) -> TaskObservation:
    if not status.exists:
        return TaskObservation(TaskPhase.MISSING)
    seen = {
        'assignee': status.assignee,
        'assignee_name': status.assignee_name,
        'created_at': _epoch(status.created_at),
    }
    state = status.state.strip().lower()
    if state == names['done'].strip().lower():
        return TaskObservation(TaskPhase.DONE, **seen)
    if state == names['review'].strip().lower():
        return TaskObservation(TaskPhase.IN_REVIEW, **seen)
    workspace = str(getattr(record, 'status', '') or '')
    if workspace == WORKSPACE_STATUS_ERRORED:
        return TaskObservation(TaskPhase.ERRORED, 'its run on this kato hit an error', **seen)
    if state == names['progress'].strip().lower():
        return TaskObservation(TaskPhase.IN_PROGRESS, **seen)
    if workspace == WORKSPACE_STATUS_PROVISIONING:
        return TaskObservation(TaskPhase.PREPARING, **seen)
    # Still in a to-do state: someone's turn (assigned) or nobody's yet.
    return TaskObservation(TaskPhase.WAITING if status.assignee else TaskPhase.UNRELEASED, **seen)


def _record(workspaces, task_id: str):
    try:
        return workspaces.get(task_id) if workspaces is not None else None
    except Exception:
        return None


def _epoch(text: str) -> float:
    try:
        return datetime.fromisoformat(str(text or '').strip().replace('Z', '+00:00')).timestamp()
    except ValueError:
        return 0.0
