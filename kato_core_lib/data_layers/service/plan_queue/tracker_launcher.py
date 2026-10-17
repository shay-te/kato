"""A plan's steps as real sub-tasks of the plan's ticket.

At approval each step becomes an UNASSIGNED sub-task (kato's pickup only takes
tickets assigned to the user it polls for, so unassigned is the hold — it
fails safe). At its turn a step is released by assigning it to that user; the
normal pickup then runs it. A sub-task someone already took is never assigned
over, and a cancelled one is left unassigned with a note — nothing is deleted.
"""

from __future__ import annotations

from plan_queue_core_lib.plan_queue_core_lib.ports import LaunchRequest

from kato_core_lib.data_layers.service.plan_queue.step_footer import subtask_description, subtask_summary
from kato_core_lib.helpers.agent_comment_classification import (
    PLAN_STEP_COMMENT_PREFIX,
    active_execution_blocking_comment,
)
from kato_core_lib.helpers.late_binding import provider_for

#: ``kato: retry approved`` clears an earlier "started working" comment, which
#: would otherwise block the pickup of a sub-task handed back and released again.
RELEASED_AGAIN_COMMENT = 'kato: retry approved — the plan queue released this task again.'
COUNTED_DONE_COMMENT = f'{PLAN_STEP_COMMENT_PREFIX}counted as done without a pull request.'
_POLLING_ALIASES = ('', 'me', 'currentuser()')


class TrackerStepLauncher(object):
    creates_at_approval = True

    def __init__(self, *, task_service, task_state_service, repository_service, ownership) -> None:
        self._get_task_service = provider_for(task_service)
        self._get_task_state_service = provider_for(task_state_service)
        self._get_repository_service = provider_for(repository_service)
        self._ownership = ownership

    def available(self) -> bool:
        return self._get_task_service().subtask_tracker() is not None

    def prepare(self, request: LaunchRequest) -> str:
        tracker = self._tracker()
        found = tracker.find_subtask(request.source_task_id, request.marker)
        if found:
            return found  # a retried create finds the first
        return tracker.create_subtask(
            request.source_task_id,
            subtask_summary(request),
            subtask_description(request, self._repository_labels(request.repositories)),
        )

    def release(self, request: LaunchRequest, task_id: str) -> str:
        tracker = self._tracker()
        status = self._status(tracker, task_id)
        if not status.exists:
            raise RuntimeError(f'{task_id} is no longer on the tracker')
        if status.assignee:
            return task_id  # someone has it: theirs, never assigned over
        if self._pickup_blocked(tracker, task_id):
            self._get_task_service().add_comment(task_id, RELEASED_AGAIN_COMMENT)
        tracker.assign_issue(task_id, self.assignee())
        return task_id

    def withdraw(self, task_id: str, reason: str) -> None:
        tracker = self._tracker()
        status = self._status(tracker, task_id)
        if not status.exists or status.assignee:
            return  # gone, or someone took it meanwhile: theirs to finish
        self._get_task_service().add_comment(
            task_id, f'{PLAN_STEP_COMMENT_PREFIX}{reason}. It stays unassigned — close it if it is not needed.',
        )

    def counted_done(self, task_id: str) -> None:
        self._get_task_state_service().move_task_to_done(task_id)
        self._get_task_service().add_comment(task_id, COUNTED_DONE_COMMENT)

    def assignee(self) -> str:
        """The user kato's pickup polls for — whom a released step goes to."""
        configured = str(self._get_task_service().bot_login or '').strip()
        if configured.lower() in _POLLING_ALIASES:
            return self._ownership.user()
        return configured

    # ----- helpers -----

    def _tracker(self):
        tracker = self._get_task_service().subtask_tracker()
        if tracker is None:
            raise RuntimeError('the issue tracker cannot hold sub-tasks')
        return tracker

    def _status(self, tracker, task_id: str):
        field = self._get_task_state_service().state_names()['field']
        return tracker.get_issues_status([task_id], field)[task_id]

    @staticmethod
    def _pickup_blocked(tracker, task_id: str) -> bool:
        """Does an earlier run's comment still block kato from picking it up?"""
        oldest_first = [{'body': comment.body} for comment in reversed(tracker.list_comments(task_id))]
        return bool(active_execution_blocking_comment(oldest_first))

    def _repository_labels(self, repository_ids) -> list[str]:
        """Each repository by the name it has on its git host (the same for
        every kato), falling back to kato's own id for it."""
        service = self._get_repository_service()
        labels = []
        for repository_id in repository_ids:
            try:
                repository = service.get_repository(repository_id)
            except Exception:
                repository = None
            labels.append(str(getattr(repository, 'repo_slug', '') or repository_id))
        return labels
