"""Where a plan's sub-task merges: the branch of the task it was split from.

A plan is one piece of work cut into small tasks. Each sub-task's pull request
goes into the main task's branch, not into the repository's own destination,
so the main task's branch gathers the whole plan and reaches the destination
in one pull request, opened from the main task.

Which task a sub-task was split from, asked in this order:

* the plan queue: every plan this kato drives or follows, in any state, so a
  plan split before sub-tasks merged this way is covered too;
* what the local step launcher said when it created the task: a local step
  starts cloning at once, before the queue has recorded which task it is;
* the footer of a tracker sub-task's description, kept in its workspace: a
  plan this kato does not follow.

The branch is the main task's branch as this kato knows it
(``task_branch_name``: its ``kato:branch:`` tag, else its id).
"""

from __future__ import annotations

import threading

from kato_core_lib.data_layers.service.plan_queue.step_footer import parse_footer
from kato_core_lib.helpers.late_binding import provider_for


class PlanMergeTarget(object):
    def __init__(self, *, queue, repository_service, workspace_manager) -> None:
        self._get_queue = provider_for(queue)
        self._get_repository_service = provider_for(repository_service)
        self._get_workspace_manager = provider_for(workspace_manager)
        self._lock = threading.Lock()
        self._announced: dict[str, str] = {}

    def announce(self, task_id: str, source_task_id: str) -> None:
        """``task_id`` was just created for a step of the plan split from
        ``source_task_id``, and the queue has not recorded it yet."""
        key = _key(task_id)
        source = str(source_task_id or '').strip()
        if key and source:
            with self._lock:
                self._announced[key] = source

    def source_of(self, task_id: str) -> str:
        """The task ``task_id`` was split from ('' when it is no plan's sub-task)."""
        key = _key(task_id)
        if not key:
            return ''
        queue = self._get_queue()
        source = queue.source_of_step_task(key) if queue is not None else ''
        with self._lock:
            if source:
                self._announced.pop(key, None)  # the queue knows it now
                return source
            source = self._announced.get(key, '')
        # The workspace folder is named by the id as written, not lower-cased.
        return source or self._source_from_footer(str(task_id).strip())

    def branch_for(self, task_id: str) -> str:
        """The branch ``task_id``'s pull requests go into ('' = the
        repository's own destination: it is no plan's sub-task)."""
        source = self.source_of(task_id)
        if not source or _key(source) == _key(task_id):
            return ''
        repository_service = self._get_repository_service()
        branch = str(repository_service.task_branch_name(source) or '').strip()
        if not branch or branch == str(repository_service.task_branch_name(task_id) or '').strip():
            return ''
        return branch

    def _source_from_footer(self, task_id: str) -> str:
        manager = self._get_workspace_manager()
        record = manager.get(task_id) if manager is not None else None
        footer = parse_footer(getattr(record, 'task_description', ''))
        return footer.source_task_id if footer is not None else ''


def _key(task_id: object) -> str:
    return str(task_id or '').strip().lower()
