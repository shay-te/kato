from __future__ import annotations

from omegaconf import DictConfig

from core_lib.data_layers.service.service import Service

from kato_core_lib.data_layers.data.task import Task
from kato_core_lib.data_layers.data_access.task_data_access import TaskDataAccess
from kato_core_lib.helpers.kato_config_utils import (
    SHARED_STATE_VALUE_DEFAULTS,
    configured_state_value,
    parse_issue_states,
)
from utils_core_lib.utils_core_lib.text_utils import alphanumeric_lower_text


class TaskService(Service):
    """Wrap ticket-system task retrieval, queue filtering, and comments."""
    # ``done`` (from the shared base) is used by
    # ``list_all_assigned_tasks`` (operator-driven task picker) so
    # completed tickets show up alongside in-flight ones. The
    # autonomous queue path doesn't query for this state; only the
    # picker does.
    _STATE_VALUE_DEFAULTS = dict(SHARED_STATE_VALUE_DEFAULTS)

    def __init__(self, config: DictConfig, task_data_access: TaskDataAccess) -> None:
        self._config = config
        self._task_data_access = task_data_access

    @property
    def provider_name(self) -> str:
        return self._task_data_access.provider_name

    @property
    def max_retries(self) -> int:
        return self._task_data_access.max_retries

    def validate_connection(self) -> None:
        self._task_data_access.validate_connection(
            assignee=self._configured_assignee(),
            states=self._configured_issue_states(),
        )

    def get_assigned_tasks(
        self,
        assignee: str | None = None,
        states: list[str] | None = None,
    ) -> list[Task]:
        return self._task_data_access.get_assigned_tasks(
            assignee=assignee or self._configured_assignee(),
            states=states or self._configured_issue_states(),
        )

    def download_image_attachments(self, issue_id: str, destination_dir) -> list[str]:
        """Save the ticket's screenshots into ``destination_dir``.

        Called once the task's workspace exists, because that is the first
        moment there is anywhere to put them. Returns the paths written.
        """
        return self._task_data_access.download_image_attachments(
            issue_id, destination_dir,
        )

    def get_review_tasks(self, assignee: str | None = None) -> list[Task]:
        return self.get_assigned_tasks(
            assignee=assignee,
            states=[self._configured_state_value('review')],
        )

    def get_started_tasks(self, assignee: str | None = None) -> list[Task]:
        """Assigned tasks past the queue: In Progress and In Review.

        ``get_assigned_tasks`` leaves both out by design — it decides what to
        START. This is for reading tags on work that is already running.
        """
        states = [
            state for state in (
                self._configured_state_value('progress'),
                self._configured_state_value('review'),
            ) if state
        ]
        if not states:
            # An empty list would fall back to the queue states above.
            return []
        return self.get_assigned_tasks(assignee=assignee, states=states)

    def list_all_assigned_tasks(
        self,
        assignee: str | None = None,
    ) -> list[Task]:
        """Every task assigned to ``assignee``, regardless of state.

        Drives the planning UI's "+ Add task" picker — the operator
        sees their full backlog (open, in-progress, in-review, done)
        and can drop any of them into kato to clone the repos and
        start a new tab. Distinct from ``get_assigned_tasks`` which
        respects the kato config's queue-state filter (used by the
        autonomous scan to decide what to *automatically* pick up).

        Implementation: union the queue states with the progress /
        review / done states so the underlying ticket-platform
        client returns one bag of tasks across the whole lifecycle.
        Deduped by id with first-seen-wins ordering so a task that
        somehow appears in multiple state buckets only renders once
        in the picker.
        """
        states = list(self._configured_issue_states())
        for key in ('progress', 'review', 'done'):
            value = self._configured_state_value(key)
            if value and value not in states:
                states.append(value)
        if not states:
            # No states configured at all → nothing to query, and
            # the data access validator would reject an empty list
            # anyway. Return empty so the picker shows "no tasks"
            # instead of crashing.
            return []
        tasks = self.get_assigned_tasks(assignee=assignee, states=states)
        seen_ids: set[str] = set()
        deduped: list[Task] = []
        for task in tasks:
            task_id = str(getattr(task, 'id', '') or '').strip()
            if not task_id or task_id in seen_ids:
                continue
            seen_ids.add(task_id)
            deduped.append(task)
        return deduped

    def get_task(self, issue_id: str):
        """One task by id — the cheap path for "which task is this?".

        Used by ``find_task_by_id`` before it resorts to walking every queue,
        which fetches and enriches the operator's entire backlog.
        """
        fetch = getattr(self._task_data_access, 'get_task', None)
        if not callable(fetch):
            return None
        return fetch(issue_id)

    def add_comment(self, issue_id: str, comment: str) -> None:
        self._task_data_access.add_comment(issue_id, comment)

    def add_pull_request_comment(self, issue_id: str, pull_request_url: str) -> None:
        self.add_comment(issue_id, f'Pull request created: {pull_request_url}')

    def add_tag(self, issue_id: str, tag_name: str) -> None:
        self._task_data_access.add_tag(issue_id, tag_name)

    def remove_tag(self, issue_id: str, tag_name: str) -> None:
        self._task_data_access.remove_tag(issue_id, tag_name)

    def _configured_assignee(self) -> str:
        return self._config.assignee

    @property
    def bot_login(self) -> str:
        """The bot's own login on the task platform (its ``assignee``).

        This is the single value every ticket platform already uses to tell a
        comment @-mentioning the bot from one @-mentioning a teammate. Surfaced
        publicly so the review-comment path can apply the same @-mention filter
        the issue-comment path already does. Empty (or the YouTrack ``"me"``
        alias) means "filter disabled".
        """
        return str(self._configured_assignee() or '')

    def _configured_issue_states(self) -> list[str]:
        configured_states = self._raw_configured_issue_states()
        filtered_states = self._exclude_non_queue_states(configured_states)
        return filtered_states

    def _raw_configured_issue_states(self) -> list[str]:
        return parse_issue_states(self._config)

    def _exclude_non_queue_states(self, states: list[str]) -> list[str]:
        non_queue_tokens = {
            self._normalized_state_token(self._configured_state_value('progress')),
            self._normalized_state_token(self._configured_state_value('review')),
        }
        filtered_states: list[str] = []
        seen_tokens: set[str] = set()
        for state in states:
            normalized_state = self._normalized_state_token(state)
            if not normalized_state or normalized_state in non_queue_tokens:
                continue
            if normalized_state in seen_tokens:
                continue
            seen_tokens.add(normalized_state)
            filtered_states.append(state)
        return filtered_states

    @staticmethod
    def _normalized_state_token(value: str) -> str:
        return alphanumeric_lower_text(value)

    def _configured_state_value(self, state_key: str) -> str:
        return configured_state_value(
            self._config, state_key, self._STATE_VALUE_DEFAULTS,
        )
