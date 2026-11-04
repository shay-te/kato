"""Which task a plan's sub-task was split from.

Asked in this order:

* the plan queue: every plan this kato drives or follows, in any state, so a
  plan split before a question was first asked here is covered too;
* what the local step launcher said when it created the task: a local step
  starts cloning at once, before the queue has recorded which task it is;
* the footer of a tracker sub-task's description, kept in its workspace: a
  plan this kato does not follow.

Both a sub-task's merge branch (``merge_target``) and its repositories
(``parent_repositories``) start from this answer.
"""

from __future__ import annotations

import threading

from kato_core_lib.data_layers.service.plan_queue.step_footer import parse_footer
from kato_core_lib.helpers.late_binding import provider_for


class PlanStepSource(object):
    def __init__(self, *, queue, workspace_manager) -> None:
        self._get_queue = provider_for(queue)
        self._get_workspace_manager = provider_for(workspace_manager)
        self._lock = threading.Lock()
        self._announced: dict[str, str] = {}

    def announce(self, task_id: str, source_task_id: str) -> None:
        """``task_id`` was just created for a step of the plan split from
        ``source_task_id``, and the queue has not recorded it yet."""
        key = task_key(task_id)
        source = str(source_task_id or '').strip()
        if key and source:
            with self._lock:
                self._announced[key] = source

    def source_of(self, task_id: str) -> str:
        """The task ``task_id`` was split from ('' when it is no plan's
        sub-task, or would be its own source)."""
        key = task_key(task_id)
        if not key:
            return ''
        queue = self._get_queue()
        source = queue.source_of_step_task(key) if queue is not None else ''
        with self._lock:
            if source:
                self._announced.pop(key, None)  # the queue knows it now
            else:
                source = self._announced.get(key, '')
        # The workspace folder is named by the id as written, not lower-cased.
        source = source or self._source_from_footer(str(task_id).strip())
        return '' if task_key(source) == key else source

    def _source_from_footer(self, task_id: str) -> str:
        manager = self._get_workspace_manager()
        record = manager.get(task_id) if manager is not None else None
        footer = parse_footer(getattr(record, 'task_description', ''))
        return footer.source_task_id if footer is not None else ''


def task_key(task_id: object) -> str:
    return str(task_id or '').strip().lower()
