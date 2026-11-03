"""Changing an approved plan: only the tasks that have not started.

A task that started keeps its step exactly as it is: its agent was told that
brief, and someone may be working from its ticket. Everything still waiting may
change: its text, what it waits for, where it works. A task may be added, or
taken out. The same validator as a proposal checks the whole result first.

Where the steps' tasks were created up front (a shared tracker), a waiting step
already has its task: an edit rewrites it, a new step gets one, and a step taken
out keeps its task, withdrawn with a note (nothing is deleted).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from plan_queue_core_lib.plan_queue_core_lib.data.plan import Plan, Step, StepStatus
from plan_queue_core_lib.plan_queue_core_lib.errors import ErrorKind, PlanQueueError
from plan_queue_core_lib.plan_queue_core_lib.graph import StepDraft, topological_order
from plan_queue_core_lib.plan_queue_core_lib.launching import build_request
from plan_queue_core_lib.plan_queue_core_lib.ports import LaunchRequest
from plan_queue_core_lib.plan_queue_core_lib.release import blocked_reason, initial_status


@dataclass(frozen=True)
class LiveEdit(object):
    """What an edit left for the host to bring its tasks in line with."""

    added: tuple[str, ...] = ()                 # new steps, which need a task
    withdrawn: tuple[str, ...] = ()             # tasks of steps taken out
    # Each waiting step's task as it read before the edit, to rewrite the
    # ones whose text changed once the new steps have their tasks.
    before: dict[str, LaunchRequest] = field(default_factory=dict)


def can_change(step: Step) -> bool:
    """A step still waiting, never handed over (nor taken by someone)."""
    return step.status in (StepStatus.BLOCKED, StepStatus.READY) and not step.released_at


def apply_live_edit(plan: Plan, drafts: Sequence[StepDraft]) -> LiveEdit:
    """Take ``drafts`` (every step, already validated) into ``plan``.

    A step that may no longer change must come back exactly as it is, and
    cannot be taken out; anything else is refused before the plan is touched.
    """
    wanted = {draft.id: draft for draft in drafts}
    for step in plan.steps:
        if not can_change(step) and (step.id not in wanted or _differs(step, wanted[step.id])):
            raise PlanQueueError(
                f'{step.task_id or step.id} has already started, so it stays as it is. '
                'Only tasks that have not started can change.',
                ErrorKind.CONFLICT,
            )
    existing = {step.id: step for step in plan.steps}
    before = {step.id: build_request(plan, step) for step in plan.steps if can_change(step) and step.task_id}
    withdrawn = tuple(step.task_id for step in plan.steps if step.id not in wanted and step.task_id)
    steps = []
    for draft in drafts:
        step = existing.get(draft.id) or Step(id=draft.id, title=draft.title, brief=draft.brief)
        if can_change(step):
            step.title = draft.title
            step.brief = draft.brief
            step.depends_on = list(draft.depends_on)
            step.repositories = list(draft.repositories)
        steps.append(step)
    plan.steps = steps
    for step in plan.steps:
        if can_change(step):
            step.status = initial_status(plan, step)
            step.reason = blocked_reason(plan, step)
    plan.touch_record()
    added = tuple(step_id for step_id in topological_order(plan.steps) if step_id not in existing)
    return LiveEdit(added=added, withdrawn=withdrawn, before=before)


def tasks_to_rewrite(plan: Plan, before: dict[str, LaunchRequest]) -> list[tuple[Step, LaunchRequest]]:
    """The waiting steps whose task would now read differently, with the new text."""
    rewrites = []
    for step_id, earlier in before.items():
        step = plan.step(step_id)
        if step is None or not step.task_id or not can_change(step):
            continue
        request = build_request(plan, step)
        if request != earlier:
            rewrites.append((step, request))
    return rewrites


def _differs(step: Step, draft: StepDraft) -> bool:
    return (step.title, step.brief, list(step.depends_on), list(step.repositories)) != (
        draft.title, draft.brief, list(draft.depends_on), list(draft.repositories)
    )
