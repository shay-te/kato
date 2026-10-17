"""The plan's order, kept by every kato's pickup.

A plan's sub-task can be assigned before its turn — a teammate takes it, or
someone assigns it to kato's user by hand. Kato's pickup does not start it
until every task it waits for is merged and done: it reads the ticket's
footer, finds (or follows) the plan, and asks the queue. A person can still
work on it by hand; only kato waits.
"""

from __future__ import annotations

from plan_queue_core_lib.plan_queue_core_lib.errors import PlanQueueError

from kato_core_lib.data_layers.service.plan_queue.step_footer import parse_footer


def plan_step_wait_reason(queue, task) -> str:
    """Why ``task`` must not start yet ('' = it may)."""
    footer = parse_footer(getattr(task, 'description', ''))
    if queue is None or footer is None:
        return ''
    task_id = str(getattr(task, 'id', '') or '')
    waiting = queue.waiting_for(task_id)
    if waiting is None:
        try:
            queue.follow_source(footer.source_task_id)
        except PlanQueueError as exc:
            return f'it is part of the plan on {footer.source_task_id}, which could not be read: {exc}'
        waiting = queue.waiting_for(task_id)
        if waiting is None:
            return f'it is part of a plan on {footer.source_task_id} that cannot be found there yet'
    if waiting:
        return f'it waits for {", ".join(waiting)} to be merged and done'
    return ''
