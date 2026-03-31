from omegaconf import DictConfig

from core_lib.data_layers.service.service import Service

from openhands_agent.data_layers.data.task import Task
from openhands_agent.data_layers.data_access.task_data_access import TaskDataAccess
from openhands_agent.text_utils import alphanumeric_lower_text, normalized_text


class TaskService(Service):
    _STATE_FIELD_DEFAULTS = {
        'progress': 'review',
        'review': 'State',
        'open': 'progress',
    }
    _STATE_VALUE_DEFAULTS = {
        'progress': 'In Progress',
        'review': 'In Review',
    }

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

    def get_review_tasks(self, assignee: str | None = None) -> list[Task]:
        return self.get_assigned_tasks(
            assignee=assignee,
            states=[self._configured_state_value('review')],
        )

    def add_comment(self, issue_id: str, comment: str) -> None:
        self._task_data_access.add_comment(issue_id, comment)

    def add_pull_request_comment(self, issue_id: str, pull_request_url: str) -> None:
        self.add_comment(issue_id, f'Pull request created: {pull_request_url}')

    def move_task_to_in_progress(self, issue_id: str) -> None:
        self._move_task_to_configured_state(issue_id, 'progress')

    def move_task_to_review(self, issue_id: str) -> None:
        self._move_task_to_configured_state(issue_id, 'review')

    def move_task_to_open(self, issue_id: str) -> None:
        self._task_data_access.move_task_to_state(
            issue_id,
            self._configured_state_field('open'),
            self._configured_open_state(),
        )

    def _move_task_to_configured_state(self, issue_id: str, state_key: str) -> None:
        self._task_data_access.move_task_to_state(
            issue_id,
            self._configured_state_field(state_key),
            self._configured_state_value(state_key),
        )

    def _configured_assignee(self) -> str:
        return self._config.assignee

    def _configured_issue_states(self) -> list[str]:
        configured_states = self._raw_configured_issue_states()
        filtered_states = self._exclude_non_queue_states(configured_states)
        return filtered_states or configured_states

    def _raw_configured_issue_states(self) -> list[str]:
        if hasattr(self._config, 'issue_states'):
            issue_states = self._config.issue_states
            if isinstance(issue_states, str):
                return [state.strip() for state in issue_states.split(',') if state.strip()]
            return [str(state).strip() for state in issue_states if str(state).strip()]
        return [self._config.issue_state]

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

    def _configured_state_field(self, state_key: str) -> str:
        config_key = f'{state_key}_state_field'
        default = self._STATE_FIELD_DEFAULTS[state_key]
        if default in self._STATE_FIELD_DEFAULTS:
            default = self._configured_state_field(default)
        return getattr(self._config, config_key, default)

    def _configured_state_value(self, state_key: str) -> str:
        return getattr(
            self._config,
            f'{state_key}_state',
            self._STATE_VALUE_DEFAULTS[state_key],
        )

    def _configured_open_state(self) -> str:
        explicit_open_state = normalized_text(getattr(self._config, 'open_state', ''))
        if explicit_open_state:
            return explicit_open_state
        configured_issue_states = self._configured_issue_states()
        if configured_issue_states:
            return configured_issue_states[0]
        return 'Open'
