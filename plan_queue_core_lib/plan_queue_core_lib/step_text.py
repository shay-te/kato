"""The description a step's task is given.

Whoever works on a step sees this text, not the plan, so it carries the step's
brief plus just enough of the plan around it: what is already merged into the
code they start from, what they wait on, and what later steps will do (so they
leave it alone).
"""

from __future__ import annotations

from plan_queue_core_lib.plan_queue_core_lib.data.plan import Plan, Step

MAX_DESCRIPTION_CHARS = 10_000


def compose_step_description(plan: Plan, step: Step) -> str:
    index = plan.index_of(step.id) + 1
    by_id = {other.id: other for other in plan.steps}
    lines = [
        f'Step {index} of {len(plan.steps)} of the plan "{plan.title}" '
        f'(split from {plan.source_task_id}).',
        '',
        step.brief,
    ]
    if step.depends_on:
        lines += ['', 'Builds on these steps, merged before this one starts:']
        lines += [f'- {_label(by_id[dep])}' for dep in step.depends_on if dep in by_id]
    later = [other for other in plan.steps[index:] if other.id != step.id]
    if later:
        lines += ['', 'Later steps of the plan (not part of this task — leave them alone):']
        lines += [f'- {_label(other)}' for other in later]
    lines += ['', 'Do only this step. Pushing and opening the pull request are done by the operator.']
    text = '\n'.join(lines)
    if len(text) <= MAX_DESCRIPTION_CHARS:
        return text
    return text[:MAX_DESCRIPTION_CHARS - 1].rstrip() + '…'


def _label(step: Step) -> str:
    return f'{step.task_id}: {step.title}' if step.task_id else step.title
