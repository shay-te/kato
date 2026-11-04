"""A plan's steps as real sub-tasks of the plan's ticket.

At approval each step becomes an UNASSIGNED sub-task (kato's pickup only takes
tickets assigned to the user it polls for, so unassigned is the hold — it
fails safe). At its turn a step is released by assigning it to that user; the
normal pickup then runs it. A sub-task someone already took is never assigned
over, and a cancelled one is left unassigned with a note — nothing is deleted.

A plan whose tasks plan first tags each sub-task ``kato:wait-planning``: every
kato's pickup then opens it in Plan mode, and it codes only once its plan is
approved in its chat. The tag is on the ticket, so it holds on any kato.
"""

from __future__ import annotations

from plan_queue_core_lib.plan_queue_core_lib.ports import LaunchRequest

from kato_core_lib.data_layers.data.fields import TaskTags
from kato_core_lib.data_layers.service.local_task_service import START_IMPLEMENT, START_PLAN
from kato_core_lib.data_layers.service.plan_queue.step_footer import subtask_description, subtask_summary
from kato_core_lib.helpers.agent_comment_classification import (
    PLAN_STEP_COMMENT_PREFIX,
    active_execution_blocking_comment,
)
from kato_core_lib.helpers.kato_tag_utils import is_model_tag, model_tag
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
        _require_taggable_model(request)  # before a ticket exists
        tracker = self._tracker()
        found = tracker.find_subtask(request.source_task_id, request.marker)
        task_id = found or tracker.create_subtask(  # a retried create finds the first
            request.source_task_id,
            subtask_summary(request),
            subtask_description(request, self._repository_labels(request.repositories)),
        )
        if request.start_mode == START_PLAN:
            self._get_task_service().add_tag(task_id, TaskTags.WAIT_PLANNING)
        self._run_on_model(task_id, request.model)
        return task_id

    def release(self, request: LaunchRequest, task_id: str) -> str:
        tracker = self._tracker()
        status = self._status(tracker, task_id)
        if not status.exists:
            raise RuntimeError(f'{task_id} is no longer on the tracker')
        if status.assignee:
            return task_id  # someone has it: theirs, never assigned over
        self._hold_for_planning(task_id, request.start_mode)
        if self._pickup_blocked(tracker, task_id):
            self._get_task_service().add_comment(task_id, RELEASED_AGAIN_COMMENT)
        tracker.assign_issue(task_id, self.assignee())
        return task_id

    def _hold_for_planning(self, task_id: str, start_mode: str) -> None:
        """Opens in Plan exactly when its start mode says so: the tag every
        kato's pickup reads. Set again here, before it is handed over, because
        the card's Plan / Start picks the mode for that one task — after the tag
        was set at creation. Adding a tag it has, or removing one it lacks,
        changes nothing. A failure fails the hand-over: the task never starts
        in a mode the operator did not pick."""
        service = self._get_task_service()
        if start_mode == START_PLAN:
            service.add_tag(task_id, TaskTags.WAIT_PLANNING)
        elif start_mode == START_IMPLEMENT:
            service.remove_tag(task_id, TaskTags.WAIT_PLANNING)

    def update(self, request: LaunchRequest, task_id: str) -> None:
        """An edited step's sub-task, rewritten: its title and its description
        with the footer (what it waits for, where it works), and the model it
        runs on."""
        _require_taggable_model(request)
        self._tracker().update_subtask(
            task_id,
            subtask_summary(request),
            subtask_description(request, self._repository_labels(request.repositories)),
        )
        self._run_on_model(task_id, request.model)

    def _run_on_model(self, task_id: str, model: str) -> None:
        """The ticket's ``kato:model:`` tag says the plan's model — the tag every
        kato's pickup reads — and no other model tag is left on it. No model:
        no model tag (the configured default runs)."""
        service = self._get_task_service()
        wanted = model_tag(model)
        try:
            current = list(getattr(service.get_task(task_id), 'tags', None) or [])
        except Exception:
            current = []  # a new ticket, or one that cannot be read: nothing to take off
        for tag in current:
            if is_model_tag(tag) and tag != wanted:
                service.remove_tag(task_id, tag)
        if wanted and wanted not in current:
            service.add_tag(task_id, wanted)

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


def _require_taggable_model(request: LaunchRequest) -> None:
    """A model the ticket cannot carry is refused, never dropped: the task
    would quietly run on the configured default instead of the one picked."""
    if request.model and not model_tag(request.model):
        raise ValueError(
            f'{request.model!r} cannot be written on a ticket as a kato:model tag, so another kato would '
            'not know it. Pick fable, opus, sonnet or haiku for this task (or leave it on the default).',
        )
