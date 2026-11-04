"""Where a plan's sub-task merges: the branch of the task it was split from.

A plan is one piece of work cut into small tasks. Each sub-task's pull request
goes into the main task's branch, not into the repository's own destination,
so the main task's branch gathers the whole plan and reaches the destination
in one pull request, opened from the main task.

Which task that is: ``step_source``. The branch is the main task's branch as
this kato knows it (``task_branch_name``: its ``kato:branch:`` tag, else its id).
"""

from __future__ import annotations

from kato_core_lib.helpers.late_binding import provider_for


class PlanMergeTarget(object):
    def __init__(self, *, step_source, repository_service) -> None:
        self._get_step_source = provider_for(step_source)
        self._get_repository_service = provider_for(repository_service)

    def branch_for(self, task_id: str) -> str:
        """The branch ``task_id``'s pull requests go into ('' = the
        repository's own destination: it is no plan's sub-task)."""
        source = self._get_step_source().source_of(task_id)
        if not source:
            return ''
        repository_service = self._get_repository_service()
        branch = str(repository_service.task_branch_name(source) or '').strip()
        if not branch or branch == str(repository_service.task_branch_name(task_id) or '').strip():
            return ''
        return branch
