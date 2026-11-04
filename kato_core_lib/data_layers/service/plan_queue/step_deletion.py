"""Delete a plan's sub-task: its open pull requests declined, then the step
taken out of the plan (the tasks waiting on it stop waiting).

Declined FIRST, and the step removed only when every one of them was: a pull
request left open after its task is gone is work nobody tracks any more — so a
decline the host refused (or a pull request API that cannot be asked) stops the
deletion, says which, and leaves the step to be deleted again. The task's own
folder, chat and containers are the task's delete (the tab's ✕, the same call),
which the board makes next.
"""

from __future__ import annotations

from kato_core_lib.helpers.late_binding import provider_for


class PlanStepDeletion(object):
    def __init__(self, *, queue, repository_service) -> None:
        self._get_queue = provider_for(queue)
        self._get_repository_service = provider_for(repository_service)

    def delete(self, plan_id: str, step_id: str) -> dict:
        """``{task_id, declined: [url], problems: [text], plan}`` — ``plan`` is
        None when a problem kept the step in the plan."""
        queue = self._get_queue()
        step = queue.get(plan_id).step(step_id)
        if step is None:
            # The queue's own refusal, in its own words.
            return {'task_id': '', 'declined': [], 'problems': [], 'plan': queue.remove_step(plan_id, step_id)}
        declined, problems = self._decline_pull_requests(step) if step.task_id else ([], [])
        if problems:
            return {'task_id': step.task_id, 'declined': declined, 'problems': problems, 'plan': None}
        plan = queue.remove_step(plan_id, step_id)
        return {'task_id': step.task_id, 'declined': declined, 'problems': [], 'plan': plan}

    def _decline_pull_requests(self, step) -> tuple[list[str], list[str]]:
        service = self._get_repository_service()
        branch = service.task_branch_name(step.task_id)
        declined, problems = [], []
        for repository_id in step.repositories:
            try:
                repository = service.get_repository(repository_id)
                for pull_request in service.decline_open_pull_requests(repository, source_branch=branch):
                    declined.append(str(pull_request.get('url') or pull_request.get('id') or repository_id))
            except Exception as exc:
                problems.append(f'{repository_id}: its pull request could not be declined — {exc}')
        return declined, problems
