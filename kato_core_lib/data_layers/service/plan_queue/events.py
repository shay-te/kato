"""One ``Mission <task>:`` log line per plan queue event.

Logged against the plan's SOURCE task, so the plan's story reads in that
task's log and the status feed can turn it into notifications.
"""

from __future__ import annotations

from plan_queue_core_lib.plan_queue_core_lib.data.plan import Plan

from kato_core_lib.helpers.mission_logging_utils import log_mission_step


def log_plan_event(logger, plan: Plan, event: str) -> None:
    if event:
        log_mission_step(logger, plan.source_task_id, 'plan queue: %s', event)
