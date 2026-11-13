"""Assign a ticket to someone — and prove it, by reading the assignee back.

THE one place kato assigns a ticket: the plan queue releasing a sub-task to the
user kato polls for, and the operator handing a task over to a teammate.
``tests/test_task_handover.py`` reads the source and fails when anything else
calls a tracker's ``assign_issue``.

A tracker that answers 200 has not necessarily assigned it (a workflow rule, a
user the project does not allow), and an assignment nobody checked is a task
nobody picks up. So the assignee is read back, as a ticket move's state is.
"""

from __future__ import annotations


def assign_verified(tracker, issue_id: str, handle: str) -> None:
    """Assign ``issue_id`` to ``handle`` (the tracker's stable handle) and read
    it back; raises when the tracker refuses or the ticket ends up with anyone
    else."""
    tracker.assign_issue(issue_id, handle)
    status = tracker.get_issues_status([issue_id], '').get(issue_id)
    assignee = str(getattr(status, 'assignee', '') or '')
    if status is None or not status.exists:
        raise RuntimeError(f'{issue_id} is no longer on the tracker')
    if assignee.strip().lower() != str(handle or '').strip().lower():
        raise RuntimeError(
            f'{issue_id} was not assigned to {handle}: the tracker shows '
            f'{assignee or "nobody"}'
        )
