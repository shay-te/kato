"""Local tasks, routed around the issue tracker — the ONE place kato tells them apart.

A local task (``LOCAL-1``) is created in kato's UI and lives in
``local_task_core_lib``'s store, not on YouTrack / Jira. Everything else in
kato talks to "the task service" and "the task state service"; these two
subclasses are wired in their place, so every caller keeps working unchanged:

* a call about a ``LOCAL-*`` id goes to the local store (read it, comment on
  it, tag it, move it between states);
* a list of tasks is the tracker's list plus the local tasks in the matching
  state. All the list methods funnel through ``get_assigned_tasks`` with the
  tracker's state names, so mapping those names back to local states here is
  enough for the scan, the review-comment poller, cleanup and recovery to see
  local tasks exactly as they see tickets in the same column. (Leaving local
  tasks out of those lists is not neutral: cleanup would mark their
  workspaces done, and their PR review comments would never be polled.)

Anything else — validating the tracker connection, the bot's login — stays the
tracker's.
"""

from __future__ import annotations

from collections.abc import Iterable

from local_task_core_lib.local_task_core_lib.data.local_task import (
    LocalTask,
    LocalTaskState,
)
from local_task_core_lib.local_task_core_lib.store import (
    LocalTaskStore,
    LocalTaskStoreError,
)
from kato_core_lib.data_layers.data.fields import TaskCommentFields
from kato_core_lib.data_layers.data.task import Task
from kato_core_lib.data_layers.service.task_service import TaskService
from kato_core_lib.data_layers.service.task_state_service import TaskStateService
from kato_core_lib.helpers.logging_utils import configure_logger

# kato's own comments on a local task — the blocking-comment gate reads the
# body; the author only says who wrote it.
LOCAL_COMMENT_AUTHOR = 'kato'

_STATE_LABELS = {
    LocalTaskState.OPEN: 'Open',
    LocalTaskState.IN_PROGRESS: 'In Progress',
    LocalTaskState.REVIEW: 'In Review',
    LocalTaskState.DONE: 'Done',
}


def local_task_as_task(local: LocalTask) -> Task:
    """A local task in the shape kato reads off a tracker ticket."""
    task = Task(
        id=local.id,
        summary=local.summary,
        description=local.description,
        tags=list(local.tags),
    )
    setattr(task, TaskCommentFields.ALL_COMMENTS, [
        {
            TaskCommentFields.AUTHOR: comment.author,
            TaskCommentFields.AUTHOR_ID: comment.author_id,
            TaskCommentFields.BODY: comment.body,
        }
        for comment in local.comments
    ])
    task.state = _STATE_LABELS[local.state]
    return task


class LocalAwareTaskService(TaskService):

    def __init__(self, config, task_data_access, local_tasks: LocalTaskStore) -> None:
        super().__init__(config, task_data_access)
        self._local_tasks = local_tasks
        self.logger = configure_logger(self.__class__.__name__)

    @property
    def local_tasks(self) -> LocalTaskStore:
        return self._local_tasks

    def is_local(self, issue_id: object) -> bool:
        return self._local_tasks.owns(issue_id)

    def get_assigned_tasks(
        self,
        assignee: str | None = None,
        states: list[str] | None = None,
    ) -> list[Task]:
        tracker = super().get_assigned_tasks(assignee=assignee, states=states)
        local = self._list_local(self._local_states(states or self._configured_issue_states()))
        return [*tracker, *local]

    def get_task(self, issue_id: str):
        if not self.is_local(issue_id):
            return super().get_task(issue_id)
        local = self._local_tasks.get(issue_id)
        return local_task_as_task(local) if local is not None else None

    def add_comment(self, issue_id: str, comment: str) -> None:
        if not self.is_local(issue_id):
            super().add_comment(issue_id, comment)
            return
        self._local_tasks.add_comment(
            issue_id, comment, author=LOCAL_COMMENT_AUTHOR, author_id=LOCAL_COMMENT_AUTHOR,
        )

    def add_pull_request_comment(self, issue_id: str, pull_request_url: str) -> None:
        if not self.is_local(issue_id):
            super().add_pull_request_comment(issue_id, pull_request_url)
            return
        self.add_comment(issue_id, f'Pull request created: {pull_request_url}')

    def add_tag(self, issue_id: str, tag_name: str) -> None:
        if not self.is_local(issue_id):
            super().add_tag(issue_id, tag_name)
            return
        self._local_tasks.add_tag(issue_id, tag_name)

    def remove_tag(self, issue_id: str, tag_name: str) -> None:
        if not self.is_local(issue_id):
            super().remove_tag(issue_id, tag_name)
            return
        self._local_tasks.remove_tag(issue_id, tag_name)

    def download_image_attachments(self, issue_id: str, destination_dir) -> list[str]:
        # A local task's images are pasted into its description, not attached.
        if self.is_local(issue_id):
            return []
        return super().download_image_attachments(issue_id, destination_dir)

    def _local_states(self, tracker_states: Iterable[str]) -> list[LocalTaskState]:
        """The local states matching the tracker states a caller asked for.

        Progress / review / done map by the configured names; every other
        requested state is a queue state, and a queued local task is OPEN.
        """
        named = {
            self._normalized_state_token(self._configured_state_value(key)): state
            for key, state in (
                ('progress', LocalTaskState.IN_PROGRESS),
                ('review', LocalTaskState.REVIEW),
                ('done', LocalTaskState.DONE),
            )
            if self._configured_state_value(key)
        }
        wanted: list[LocalTaskState] = []
        for value in tracker_states:
            state = named.get(self._normalized_state_token(value), LocalTaskState.OPEN)
            if state not in wanted:
                wanted.append(state)
        return wanted

    def _list_local(self, states: list[LocalTaskState]) -> list[Task]:
        """Local tasks in ``states``; an unreadable store is logged, not fatal,
        so one bad file never stops the tracker's tasks from being worked."""
        try:
            return [local_task_as_task(task) for task in self._local_tasks.list(states)]
        except LocalTaskStoreError:
            self.logger.exception('local tasks are unreadable; listing tracker tasks only')
            return []


class LocalAwareTaskStateService(TaskStateService):

    def __init__(self, config, task_data_access, local_tasks: LocalTaskStore) -> None:
        super().__init__(config, task_data_access)
        self._local_tasks = local_tasks

    def move_task_to_in_progress(self, issue_id: str) -> None:
        if not self._move_local(issue_id, LocalTaskState.IN_PROGRESS):
            super().move_task_to_in_progress(issue_id)

    def move_task_to_review(self, issue_id: str) -> None:
        if not self._move_local(issue_id, LocalTaskState.REVIEW):
            super().move_task_to_review(issue_id)

    def move_task_to_done(self, issue_id: str) -> None:
        if not self._move_local(issue_id, LocalTaskState.DONE):
            super().move_task_to_done(issue_id)

    def move_task_to_open(self, issue_id: str) -> None:
        if not self._move_local(issue_id, LocalTaskState.OPEN):
            super().move_task_to_open(issue_id)

    def _move_local(self, issue_id: str, state: LocalTaskState) -> bool:
        if not self._local_tasks.owns(issue_id):
            return False
        self._local_tasks.set_state(issue_id, state)
        return True
