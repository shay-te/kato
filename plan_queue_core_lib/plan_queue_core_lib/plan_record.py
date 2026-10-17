"""A tracker plan as text every host can read — and the rules for trusting it.

The driver publishes its plan on the tracker (the host decides where: a
comment on the plan's task, in a code block). Another host — a teammate's —
reads it back to show the same board. The text is one header line and one line
per step (where it works, what it waits for, and "done" once the driver counts
it satisfied — so a host without that repository still sees it done)::

    plan 7f3a…c2 for PROJ-1 · status running · driven by 1b2c… (alice)
    PROJ-2 | api | Add the endpoint | in api | waits for — | done
    PROJ-3 | ui | Use it in the page | in web | waits for PROJ-2

Anyone who can comment can post such text, so it is trusted only so far:
the driver posts its own record (its author is the user it names), every task
in it is a sub-task of the plan's task (the host checks that — it can ask the
tracker), and a later record of the same plan may not change what waits for
what (only who drives it, its status, what is done, and a re-created step's task).
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from plan_queue_core_lib.plan_queue_core_lib.data.plan import Plan
from plan_queue_core_lib.plan_queue_core_lib.graph import (
    SplitValidationError,
    StepDraft,
    topological_order,
)
from plan_queue_core_lib.plan_queue_core_lib.ports import PlanRecord, RecordStep

NO_WAIT = '—'
_HEADER = re.compile(
    r'^plan (?P<plan>[0-9a-f]{32}) for (?P<source>\S+) · status (?P<status>[a-z_]+)'
    r' · driven by (?P<driver>\S+) \((?P<user>[^()]*)\)$'
)
_STEP = re.compile(
    r'^(?P<task>[^\s|]+) \| (?P<step>[a-z][a-z0-9_-]{0,23}) \| (?P<title>[^|]*)'
    r' \| in (?P<repos>[^|]+) \| waits for (?P<waits>[^|]+?)(?P<done> \| done)?$'
)


def render_record(plan: Plan) -> str:
    """The plan as record text (steps that have a task, in plan order)."""
    by_id = {step.id: step for step in plan.steps}
    lines = [
        f'plan {plan.plan_id} for {plan.source_task_id} · status {plan.status.value}'
        f' · driven by {plan.driver} ({_clean(plan.driver_user)})',
    ]
    for step in plan.steps:
        if not step.task_id:
            continue
        waits = [by_id[dep].task_id for dep in step.depends_on if dep in by_id and by_id[dep].task_id]
        repos = ', '.join(_clean(repo) for repo in step.repositories) or NO_WAIT
        done = ' | done' if step.is_satisfied else ''
        lines.append(
            f'{step.task_id} | {step.id} | {_clean(step.title)} | in {repos}'
            f' | waits for {", ".join(waits) or NO_WAIT}{done}',
        )
    return '\n'.join(lines)


def parse_record(text: str, *, author: str = '') -> PlanRecord | None:
    """The record in ``text``, or None when there is no readable one.

    Strict: a header without a single step line, a step line that names a task
    the record does not hold, a repeated task or step, or steps that wait for
    each other in a circle, is no record.
    """
    lines = [line.strip() for line in str(text or '').splitlines()]
    for index, line in enumerate(lines):
        header = _HEADER.match(line)
        if header:
            return _record(header, _step_lines(lines[index + 1:]), author)
    return None


def acceptable(record: PlanRecord, *, previous: PlanRecord | None) -> str:
    """'' when ``record`` may be followed after ``previous``, else why not."""
    if not record.author or record.author != record.driver_user:
        return f'the record was posted by {record.author or "nobody"}, not by its driver {record.driver_user}'
    if previous is None or previous.plan_id != record.plan_id:
        return ''
    if _graph(previous) != _graph(record):
        return 'the record changes which step waits for which'
    return ''


def _step_lines(lines: Iterable[str]) -> list[str]:
    found = []
    for line in lines:
        if not line or line.startswith('```'):
            if found:
                break
            continue
        if not _STEP.match(line):
            break
        found.append(line)
    return found


def _record(header: re.Match, step_lines: list[str], author: str) -> PlanRecord | None:
    steps = []
    for line in step_lines:
        match = _STEP.match(line)
        steps.append(RecordStep(
            task_id=match.group('task'),
            step_id=match.group('step'),
            title=match.group('title').strip(),
            waits_for=_listed(match.group('waits')),
            repositories=_listed(match.group('repos')),
            done=bool(match.group('done')),
        ))
    if not steps or not _consistent(steps):
        return None
    return PlanRecord(
        plan_id=header.group('plan'),
        source_task_id=header.group('source'),
        status=header.group('status'),
        driver=header.group('driver'),
        driver_user=header.group('user').strip(),
        steps=tuple(steps),
        author=str(author or ''),
    )


def _consistent(steps: list[RecordStep]) -> bool:
    tasks = [step.task_id for step in steps]
    ids = [step.step_id for step in steps]
    if len(set(tasks)) != len(tasks) or len(set(ids)) != len(ids):
        return False
    step_of = dict(zip(tasks, ids))
    if any(task not in step_of for step in steps for task in step.waits_for):
        return False
    drafts = [
        StepDraft(step.step_id, step.title, '', depends_on=tuple(step_of[task] for task in step.waits_for))
        for step in steps
    ]
    try:
        topological_order(drafts)
    except SplitValidationError:
        return False
    return True


def _graph(record: PlanRecord) -> dict[str, frozenset[str]]:
    """What waits for what, by step id (task ids may change on a re-create)."""
    step_of = {step.task_id: step.step_id for step in record.steps}
    return {step.step_id: frozenset(step_of[task] for task in step.waits_for) for step in record.steps}


def _listed(text: str) -> tuple[str, ...]:
    text = text.strip()
    if text == NO_WAIT:
        return ()
    return tuple(item.strip() for item in text.split(',') if item.strip())


def _clean(text: str) -> str:
    """One line, no column separator."""
    return ' '.join(str(text or '').replace('|', '/').replace('(', '[').replace(')', ']').split())
