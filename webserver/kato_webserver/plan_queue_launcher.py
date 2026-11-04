"""Start a plan step as a local kato task (no tracker).

A local step's task is created only when the step is released — its
repositories are cloned fresh then, from the branch of the task the plan was
split from (where its parents merged), so it starts from the code they already
merged. It is started through the same ``LocalTaskStarter`` the "New
task" tab uses: created IN_PROGRESS (the autonomous scan never picks it up),
cloned, branched, and its chat opened in the plan's start mode.
"""

from __future__ import annotations

from collections.abc import Callable

from flask import Flask

from kato_core_lib.data_layers.service.local_task_service import (
    START_IMPLEMENT,
    START_MODES,
    LocalTaskRejected,
)
from plan_queue_core_lib.plan_queue_core_lib.ports import LaunchRefused, LaunchRequest


def local_step_title(request: LaunchRequest) -> str:
    """"UNA-1234 · Task 2: Add the endpoint" — where the step comes from."""
    return f'{request.source_task_id} · Task {request.index}: {request.title}'


class LocalStepLauncher(object):
    creates_at_approval = False

    def __init__(self, app: Flask, *, announce_task: Callable[[str, str], None] | None = None) -> None:
        self._app = app
        # ``announce_task(task_id, source_task_id)``: the new task is a step of
        # the plan split from ``source_task_id``, said BEFORE it starts —
        # cloning begins at once, and the queue records the task only after
        # ``release`` returns. Without it the first clone is cut from the
        # repository's destination instead of the plan's branch.
        self._announce_task = announce_task

    def _local_tasks(self):
        return getattr(self._app.config.get('AGENT_SERVICE'), 'local_tasks', None)

    def _starter(self):
        return self._app.config.get('LOCAL_TASK_STARTER')

    def available(self) -> bool:
        return self._local_tasks() is not None and self._starter() is not None

    def prepare(self, request: LaunchRequest) -> str:
        raise LaunchRefused('a local step is created when it is released, not before')

    def release(self, request: LaunchRequest, task_id: str) -> str:
        """Create the step's task and start it; always a new task (a retry
        after a failed start must not reuse the broken one)."""
        service, starter = self._local_tasks(), self._starter()
        if service is None or starter is None:
            raise LaunchRefused('creating tasks in kato is not available right now')
        try:
            task = service.create(local_step_title(request), request.description, list(request.repositories))
        except LocalTaskRejected as exc:
            raise LaunchRefused(str(exc)) from exc
        if self._announce_task is not None:
            self._announce_task(str(task.id), request.source_task_id)
        start_mode = request.start_mode if request.start_mode in START_MODES else START_IMPLEMENT
        starter.start(str(task.id), start_mode, model=request.model, effort=request.effort)
        return str(task.id)

    def update(self, request: LaunchRequest, task_id: str) -> None:
        """Nothing to do: a local step's task never exists before release."""

    def withdraw(self, task_id: str, reason: str) -> None:
        """Nothing to do: a local step's task never exists before release."""
