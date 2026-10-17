"""Which steps may start: the dependency rule, and nothing else.

A step may start once every step it depends on is satisfied. There is no cap
of our own — the host's limits on how much runs at once apply as they would to
any task. Steps become ready in the order they were listed, so releases are
predictable.
"""

from __future__ import annotations

from plan_queue_core_lib.plan_queue_core_lib.data.plan import Plan, Step, StepStatus


def waiting_on(plan: Plan, step: Step) -> list[str]:
    """The steps ``step`` still waits for (empty when it may start)."""
    blocking = []
    for dep in step.depends_on:
        parent = plan.step(dep)
        if parent is None or not parent.is_satisfied:
            blocking.append(dep)
    return blocking


def unblocked(plan: Plan) -> list[Step]:
    """Blocked steps whose dependencies are now all satisfied."""
    return [
        step for step in plan.steps
        if step.status == StepStatus.BLOCKED and not waiting_on(plan, step)
    ]


def releasable(plan: Plan) -> list[Step]:
    """Ready steps, in plan order."""
    return [step for step in plan.steps if step.status == StepStatus.READY]


def initial_status(plan: Plan, step: Step) -> StepStatus:
    return StepStatus.BLOCKED if waiting_on(plan, step) else StepStatus.READY


def blocked_reason(plan: Plan, step: Step) -> str:
    names = []
    for dep in waiting_on(plan, step):
        parent = plan.step(dep)
        names.append(parent.task_id or parent.title if parent else dep)
    return f'waits for {", ".join(names)}' if names else ''


def is_finished(plan: Plan) -> bool:
    """Every step satisfied or cancelled (and there is at least one)."""
    return bool(plan.steps) and all(step.status.is_terminal for step in plan.steps)
