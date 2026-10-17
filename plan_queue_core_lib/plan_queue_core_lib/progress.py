"""Move a step along as its task progresses.

Pure: given a step and what the host observed about its task, say what the
step's status should now be. The service applies the answer (only if nothing
else changed the step meanwhile).

A step whose task exists before its turn (a tracker sub-task, created at
approval and held unassigned) is watched too: when someone takes it early —
assigns it, starts it, finishes it — the step follows, so the board shows who
has it. A task handed back (unassigned again) returns the step to the queue.
"""

from __future__ import annotations

from dataclasses import dataclass

from plan_queue_core_lib.plan_queue_core_lib.data.plan import FailReason, SatisfiedBy, Step, StepStatus
from plan_queue_core_lib.plan_queue_core_lib.ports import TaskObservation, TaskPhase


@dataclass(frozen=True)
class StepChange(object):
    status: StepStatus
    reason_code: str = ''
    reason: str = ''
    satisfied_by: str = ''   # set when the change satisfies the step
    taken: bool = False      # the step's task was taken before the step was released


# A release that has not shown up yet may simply be in flight (a tracker's
# read can lag a write); only after this long is it treated as never landed.
UNRELEASED_GRACE_SECONDS = 120
HANDED_BACK_REASON = 'handed back; it will be released again'
TAKEN_EARLY_REASON = 'taken before its turn'


def change_from_observation(
    step: Step, observation: TaskObservation, *, now: float,
) -> StepChange | None:
    """The step's new status, or None when it stays as it is.

    A step already waiting for its merge stays there unless its task
    disappears or the driver counted it as done (the merge decides the rest).
    """
    if step.status.is_terminal or step.status == StepStatus.FAILED:
        return None
    phase = observation.phase
    if phase in (TaskPhase.MISSING, TaskPhase.GONE):
        return StepChange(
            StepStatus.FAILED, FailReason.TASK_GONE.value,
            f'{step.task_id} was removed before it was done',
        )
    if phase == TaskPhase.DONE and observation.counted_done:
        return StepChange(
            StepStatus.SATISFIED, '', 'counted as done by the operator',
            satisfied_by=SatisfiedBy.OPERATOR.value, taken=not step.status.is_released,
        )
    if not step.status.is_released:
        return _taken_early(step, observation)
    if step.status == StepStatus.AWAITING_MERGE:
        return None
    if phase == TaskPhase.UNRELEASED:
        return _not_handed_over(step, now)
    if phase == TaskPhase.DONE:
        return _differs(step, StepChange(StepStatus.AWAITING_MERGE))
    if phase == TaskPhase.ERRORED:
        if step.status == StepStatus.STARTING:
            return StepChange(
                StepStatus.FAILED, FailReason.LAUNCH_FAILED.value,
                observation.detail or f'{step.task_id} could not be started',
            )
        return _differs(step, StepChange(step.status, '', observation.detail or 'its run hit an error'))
    target = _PHASE_STATUS.get(phase)
    if target is None:
        return None
    return _differs(step, StepChange(target))


def _not_handed_over(step: Step, now: float) -> StepChange | None:
    """Released by the driver but nobody has the task: a release that never
    landed (a restart between the two), or one handed back. Either way the
    step goes back in line — after a grace for a release still in flight."""
    if step.status == StepStatus.STARTING:
        if now - step.released_at >= UNRELEASED_GRACE_SECONDS:
            return StepChange(StepStatus.READY, '', 'not handed over yet; it will be retried')
        return None
    return StepChange(StepStatus.READY, '', HANDED_BACK_REASON)


def _taken_early(step: Step, observation: TaskObservation) -> StepChange | None:
    """A step not released yet whose task someone has already taken."""
    phase = observation.phase
    if phase == TaskPhase.WAITING and not observation.assignee:
        return None
    target = _EARLY_STATUS.get(phase)
    if target is None:
        return None
    reason = TAKEN_EARLY_REASON if step.status == StepStatus.BLOCKED else ''
    return StepChange(target, '', reason, taken=True)


_PHASE_STATUS = {
    TaskPhase.WAITING: StepStatus.STARTING,
    TaskPhase.PREPARING: StepStatus.STARTING,
    TaskPhase.IN_PROGRESS: StepStatus.ACTIVE,
    TaskPhase.IN_REVIEW: StepStatus.IN_REVIEW,
}

_EARLY_STATUS = {
    TaskPhase.WAITING: StepStatus.STARTING,
    TaskPhase.IN_PROGRESS: StepStatus.ACTIVE,
    TaskPhase.IN_REVIEW: StepStatus.IN_REVIEW,
    TaskPhase.DONE: StepStatus.AWAITING_MERGE,
}


def _differs(step: Step, change: StepChange) -> StepChange | None:
    if (step.status, step.reason_code, step.reason) == (change.status, change.reason_code, change.reason):
        return None
    return change
