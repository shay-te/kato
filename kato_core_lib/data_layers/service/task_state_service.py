from omegaconf import DictConfig

from core_lib.data_layers.service.service import Service

from kato_core_lib.data_layers.data_access.task_data_access import TaskDataAccess
from kato_core_lib.helpers.kato_config_utils import (
    SHARED_STATE_VALUE_DEFAULTS,
    configured_state_value,
    parse_issue_states,
)
from utils_core_lib.utils_core_lib.text_utils import normalized_text


class TaskStateService(Service):
    """Wrap ticket-system task state transitions."""
    _STATE_FIELD_DEFAULTS = {
        'progress': 'review',
        'review': 'State',
        'open': 'progress',
        # A tracker that closes a ticket through a different field than
        # the review transition sets ``done_state_field`` explicitly
        # (GitHub/GitLab: ``state`` rather than the ``labels`` field
        # their review transition writes). Unset, it follows review's
        # field — the common case on YouTrack/Jira, where every
        # workflow move touches the same State/status field.
        'done': 'review',
    }
    _STATE_VALUE_DEFAULTS = dict(SHARED_STATE_VALUE_DEFAULTS)

    def __init__(self, config: DictConfig, task_data_access: TaskDataAccess) -> None:
        self._config = config
        self._task_data_access = task_data_access

    def move_task_to_in_progress(self, issue_id: str) -> None:
        self._move_task_to_configured_state(issue_id, 'progress')

    def move_task_to_review(self, issue_id: str) -> None:
        self._move_task_to_configured_state(issue_id, 'review')

    def move_task_to_done(self, issue_id: str) -> None:
        """Move a ticket to the tracker's done/closed column.

        Operator-triggered ONLY (the "this task is done" checkbox on the
        forget dialog). The autonomous flow never closes a ticket —
        deciding that work is finished is the reviewer's call, same as
        the never-auto-resolve rule for PR comment threads.
        """
        self._move_task_to_configured_state(issue_id, 'done')

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

    def _configured_state_field(self, state_key: str) -> str:
        return self._resolve_state_field(state_key, frozenset())

    def _resolve_state_field(self, state_key: str, visited: frozenset[str]) -> str:
        """The tracker field name that ``state_key``'s transition writes.

        A BLANK configured value counts as unset and follows the chain of
        defaults, exactly as a missing key does. ``getattr`` alone could not
        do that: the config layer resolves
        ``${oc.env:YOUTRACK_PROGRESS_STATE_FIELD,"State"}`` to ``''`` when
        that variable is set-but-empty — which is what a field the operator
        cleared in the Settings UI looks like — so the default never
        applied. The empty name then reached the tracker client and
        ``move_issue_to_state`` raised ``missing issue field id for: ''``.

        That single blank stopped kato dead: ``_start_task_processing``
        moves the ticket to In Progress BEFORE running the agent, so the
        failed transition left every task in Open and the agent never
        started — the "kato does not auto start new tasks" report.
        """
        config_key = f'{state_key}_state_field'
        default = self._STATE_FIELD_DEFAULTS[state_key]
        visited = visited | {state_key}
        if default in self._STATE_FIELD_DEFAULTS and default not in visited:
            default = self._resolve_state_field(default, visited)
        return normalized_text(getattr(self._config, config_key, '')) or default

    def _configured_state_value(self, state_key: str) -> str:
        return configured_state_value(
            self._config, state_key, self._STATE_VALUE_DEFAULTS,
        )

    def _configured_open_state(self) -> str:
        explicit_open_state = normalized_text(getattr(self._config, 'open_state', ''))
        if explicit_open_state:
            return explicit_open_state
        configured_issue_states = self._configured_issue_states()
        if configured_issue_states:
            return configured_issue_states[0]
        return 'Open'

    def _configured_issue_states(self) -> list[str]:
        return parse_issue_states(self._config)
