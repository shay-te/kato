"""The plan's record as a comment on the plan's ticket — written and read back.

The driver keeps ONE comment: it writes the record once and rewrites that same
comment as the plan moves (a step done, a task re-created, stopped), so the
ticket gains one comment, not one per change. A kato that takes the plan over
writes its own. Readers take the newest readable record whose every task is a
sub-task of the ticket (the tracker says so — anyone can post text, but not a
parent link); the queue then checks its author and that it keeps the order.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from plan_queue_core_lib.plan_queue_core_lib.data.plan import Plan
from plan_queue_core_lib.plan_queue_core_lib.plan_record import parse_record, render_record
from plan_queue_core_lib.plan_queue_core_lib.ports import PlanRecord

from kato_core_lib.helpers.agent_comment_classification import PLAN_RECORD_COMMENT_PREFIX
from kato_core_lib.helpers.late_binding import provider_for

READ_EVERY_SECONDS = 60


def record_intro(plan: Plan) -> str:
    count = sum(1 for step in plan.steps if step.task_id)
    return (
        f'{PLAN_RECORD_COMMENT_PREFIX}{plan.source_task_id} is split into {count} sub-tasks. '
        'Each one starts only when every task it waits for is merged and done; '
        'whoever has a sub-task works on it, and the kato named below releases the next.'
    )


class TrackerRecordPublisher(object):
    def __init__(self, *, task_service, ownership) -> None:
        self._get_task_service = provider_for(task_service)
        self._ownership = ownership
        self._comment_ids: dict[str, str] = {}

    def publish(self, plan: Plan) -> None:
        tracker = self._get_task_service().subtask_tracker()
        if tracker is None:
            raise RuntimeError('the issue tracker cannot hold sub-tasks')
        intro, text = record_intro(plan), render_record(plan)
        comment_id = self._comment_ids.get(plan.plan_id) or self._own_record(tracker, plan)
        if comment_id:
            tracker.update_preformatted_comment(plan.source_task_id, comment_id, intro, text)
        else:
            comment_id = tracker.add_preformatted_comment(plan.source_task_id, intro, text)
        if comment_id:
            self._comment_ids[plan.plan_id] = comment_id

    def _own_record(self, tracker, plan: Plan) -> str:
        """This account's earlier record of the plan (after a restart)."""
        me = self._ownership.user()
        for comment in tracker.list_comments(plan.source_task_id):
            record = parse_record(comment.body, author=comment.author)
            if comment.author == me and record is not None and record.plan_id == plan.plan_id:
                return comment.comment_id
        return ''


class TrackerRecordSource(object):
    def __init__(self, *, task_service, task_state_service, clock: Callable[[], float] = time.time) -> None:
        self._get_task_service = provider_for(task_service)
        self._get_task_state_service = provider_for(task_state_service)
        self._clock = clock
        self._read: dict[str, tuple[float, PlanRecord | None]] = {}

    def latest(self, source_task_id: str) -> PlanRecord | None:
        key = str(source_task_id or '').strip().lower()
        read_at, record = self._read.get(key, (-1e18, None))
        if self._clock() - read_at < READ_EVERY_SECONDS:
            return record
        record = self._newest(source_task_id)
        self._read[key] = (self._clock(), record)
        return record

    def forget(self, source_task_id: str) -> None:
        """Read the ticket again next time (the operator asked to look now)."""
        self._read.pop(str(source_task_id or '').strip().lower(), None)

    def _newest(self, source_task_id: str) -> PlanRecord | None:
        tracker = self._get_task_service().subtask_tracker()
        if tracker is None:
            return None
        for comment in tracker.list_comments(source_task_id):
            record = parse_record(comment.body, author=comment.author)
            if record is not None and self._belongs(tracker, record, source_task_id):
                return record
        return None

    def _belongs(self, tracker, record: PlanRecord, source_task_id: str) -> bool:
        """The record is about this ticket and every task in it is one of its
        sub-tasks — never a ticket anyone merely named."""
        source = source_task_id.strip().lower()
        if record.source_task_id.lower() != source:
            return False
        field = self._get_task_state_service().state_names()['field']
        statuses = tracker.get_issues_status([step.task_id for step in record.steps], field)
        for step in record.steps:
            status = statuses.get(step.task_id)
            if status is None or not status.exists or status.parent_id.strip().lower() != source:
                return False
        return True
