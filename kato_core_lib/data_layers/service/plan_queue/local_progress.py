"""Where a local (no-tracker) step's task stands.

Read from the two durable records kato keeps for a local task: its state in
the local task store (DONE survives the workspace being deleted) and its
workspace record's status.
"""

from __future__ import annotations

from collections.abc import Sequence

from local_task_core_lib.local_task_core_lib.data.local_task import LocalTaskState
from plan_queue_core_lib.plan_queue_core_lib.ports import TaskObservation, TaskPhase
from workspace_core_lib.workspace_core_lib.data_layers.data.workspace_record import (
    WORKSPACE_STATUS_ERRORED,
    WORKSPACE_STATUS_PROVISIONING,
    WORKSPACE_STATUS_REVIEW,
)

from kato_core_lib.helpers.late_binding import provider_for


class LocalTaskProgress(object):
    def __init__(self, *, local_task_store, workspace_manager) -> None:
        self._get_store = provider_for(local_task_store)
        self._get_workspace_manager = provider_for(workspace_manager)

    def observe(self, task_ids: Sequence[str]) -> dict[str, TaskObservation]:
        store = self._get_store()
        workspaces = self._get_workspace_manager()
        observed = {}
        for task_id in task_ids:
            task = store.get(task_id) if store is not None else None
            if task is None:
                observed[task_id] = TaskObservation(TaskPhase.MISSING)
                continue
            if task.state == LocalTaskState.DONE:
                observed[task_id] = TaskObservation(TaskPhase.DONE)
                continue
            record = workspaces.get(task_id) if workspaces is not None else None
            observed[task_id] = _phase(task.state, record)
        return observed


def _phase(state: LocalTaskState, record) -> TaskObservation:
    if record is None:
        return TaskObservation(TaskPhase.GONE)
    status = str(getattr(record, 'status', '') or '')
    if status == WORKSPACE_STATUS_ERRORED:
        return TaskObservation(TaskPhase.ERRORED, 'the task\'s workspace hit an error')
    if status == WORKSPACE_STATUS_PROVISIONING:
        return TaskObservation(TaskPhase.PREPARING)
    if state == LocalTaskState.REVIEW or status == WORKSPACE_STATUS_REVIEW:
        return TaskObservation(TaskPhase.IN_REVIEW)
    return TaskObservation(TaskPhase.IN_PROGRESS)
