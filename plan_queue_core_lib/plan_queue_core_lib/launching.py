"""Hand steps to the host: create their tasks, start them, withdraw them.

Every call to the host goes through here, so a host failure always becomes the
same thing: an outcome with a reason code and a message, never an exception
leaking into the queue's bookkeeping.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from plan_queue_core_lib.plan_queue_core_lib.data.plan import FailReason, Plan, Step
from plan_queue_core_lib.plan_queue_core_lib.ports import LaunchRefused, LaunchRequest, TaskLauncher
from plan_queue_core_lib.plan_queue_core_lib.step_text import compose_step_description


@dataclass(frozen=True)
class LaunchOutcome(object):
    task_id: str = ''
    fail_reason: FailReason | None = None
    message: str = ''

    @property
    def ok(self) -> bool:
        return self.fail_reason is None


def build_request(plan: Plan, step: Step) -> LaunchRequest:
    by_id = {other.id: other for other in plan.steps}
    waits_for = tuple(
        by_id[dep].task_id for dep in step.depends_on if dep in by_id and by_id[dep].task_id
    )
    return LaunchRequest(
        plan_id=plan.plan_id,
        step_id=step.id,
        index=plan.index_of(step.id) + 1,
        count=len(plan.steps),
        title=step.title,
        description=compose_step_description(plan, step),
        repositories=tuple(step.repositories),
        source_task_id=plan.source_task_id,
        plan_title=plan.title,
        start_mode=plan.start_mode,
        model=plan.step_model,
        effort=plan.effort,
        waits_for=waits_for,
    )


def prepare_task(launcher: TaskLauncher, request: LaunchRequest) -> LaunchOutcome:
    """Create the step's task (not started). A failure is ``CREATE_FAILED``."""
    try:
        task_id = str(launcher.prepare(request) or '')
    except Exception as exc:
        return LaunchOutcome(fail_reason=FailReason.CREATE_FAILED, message=_message(exc))
    if not task_id:
        return LaunchOutcome(
            fail_reason=FailReason.CREATE_FAILED, message='the task was not created',
        )
    return LaunchOutcome(task_id=task_id)


def release_task(launcher: TaskLauncher, request: LaunchRequest, task_id: str) -> LaunchOutcome:
    """Start the step. A reason the operator can fix is ``LAUNCH_REFUSED``."""
    try:
        started = str(launcher.release(request, task_id) or '')
    except LaunchRefused as exc:
        return LaunchOutcome(fail_reason=FailReason.LAUNCH_REFUSED, message=_message(exc))
    except Exception as exc:
        return LaunchOutcome(fail_reason=FailReason.LAUNCH_FAILED, message=_message(exc))
    if not started:
        return LaunchOutcome(
            fail_reason=FailReason.LAUNCH_FAILED, message='the task was not started',
        )
    return LaunchOutcome(task_id=started)


def count_done(launcher: TaskLauncher | None, task_id: str) -> None:
    """Mark a step's task done where everyone can see it. Raises the host's
    error: a step counted done must not look done only here."""
    if not task_id:
        return  # no task anywhere: nothing for anyone else to see
    if launcher is None:
        raise RuntimeError('its tracker cannot be reached from here')
    launcher.counted_done(task_id)


def withdraw_task(
    launcher: TaskLauncher | None, task_id: str, reason: str, logger: logging.Logger,
) -> None:
    """Tell the host a prepared task won't run. Best effort, never raises."""
    if launcher is None or not task_id:
        return
    try:
        launcher.withdraw(task_id, reason)
    except Exception:
        logger.warning('could not leave a note on %s that it will not run', task_id)


def _message(exc: Exception) -> str:
    return str(exc) or type(exc).__name__
