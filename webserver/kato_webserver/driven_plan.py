"""The plan THIS kato drives that was split from a task, if any.

ONE answer for both places that ask: deleting the task cascades into that
plan (its sub-tasks deleted, their pull requests declined — ``app.py``'s
``_forget_cascaded_sub_tasks``), and the hand-over's "also delete it from my
tasks" is not offered for it (``handover_routes``), because a teammate taking
the task over must never cost the plan its sub-tasks. A plan this kato only
FOLLOWS is another kato's: nothing here touches it, so it does not count.
"""

from __future__ import annotations

from flask import Flask
from plan_queue_core_lib.plan_queue_core_lib.data.plan import PlanMode


def plan_driven_from(app: Flask, task_id: str):
    """The newest plan split from ``task_id`` that this kato drives, else None."""
    queue = getattr(app.config.get('AGENT_SERVICE'), 'plan_queue', None)
    if queue is None:
        return None
    plan = queue.plan_for_source(task_id)
    if plan is None or plan.mode == PlanMode.FOLLOWED:
        return None
    return plan
