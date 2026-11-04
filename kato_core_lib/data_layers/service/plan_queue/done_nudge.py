"""A plan's sub-task reached Done: its board reads it again now.

Told by ``DoneAnnouncements`` (the one "on done" fan-out) — so the Done button,
"Finish & forget" with done and a board's "Count as done" all move the card the
same way. Without it the board waited out the tracker cache (a minute) before
the card left "In progress".
"""

from __future__ import annotations

from kato_core_lib.helpers.late_binding import provider_for


class PlanQueueDoneNudge(object):
    def __init__(self, *, agent_service) -> None:
        self._get_agent_service = provider_for(agent_service)

    def task_done(self, task_id: str) -> None:
        queue = getattr(self._get_agent_service(), 'plan_queue', None)
        if queue is not None and queue.source_of_step_task(task_id):
            queue.task_changed(task_id)
