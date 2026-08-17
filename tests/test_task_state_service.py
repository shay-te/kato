import types
import unittest
from unittest.mock import Mock

from kato_core_lib.data_layers.data_access.task_data_access import TaskDataAccess
from kato_core_lib.data_layers.service.task_state_service import TaskStateService


class TaskStateServiceTests(unittest.TestCase):
    def test_uses_configured_queue_states_and_state_transitions(self) -> None:
        config = types.SimpleNamespace(
            base_url="https://youtrack.example",
            token="yt-token",
            project="PROJ",
            assignee="me",
            issue_states=["Todo", "Open"],
        )
        client = Mock()

        task_state_service = TaskStateService(config, TaskDataAccess(config, client))
        task_state_service.move_task_to_in_progress('PROJ-1')
        task_state_service.move_task_to_review('PROJ-1')
        task_state_service.move_task_to_open('PROJ-1')

        self.assertEqual(
            client.move_issue_to_state.call_args_list,
            [
                unittest.mock.call('PROJ-1', 'State', 'In Progress'),
                unittest.mock.call('PROJ-1', 'State', 'In Review'),
                unittest.mock.call('PROJ-1', 'State', 'Todo'),
            ],
        )

    def test_uses_legacy_issue_state_and_default_review_config(self) -> None:
        config = types.SimpleNamespace(
            base_url="https://youtrack.example",
            token="yt-token",
            project="PROJ",
            assignee="me",
            issue_state="Todo",
        )
        client = Mock()

        task_state_service = TaskStateService(config, TaskDataAccess(config, client))
        task_state_service.move_task_to_in_progress('PROJ-1')
        task_state_service.move_task_to_review('PROJ-1')
        task_state_service.move_task_to_open('PROJ-1')

        self.assertEqual(
            client.move_issue_to_state.call_args_list,
            [
                unittest.mock.call('PROJ-1', 'State', 'In Progress'),
                unittest.mock.call('PROJ-1', 'State', 'In Review'),
                unittest.mock.call('PROJ-1', 'State', 'Todo'),
            ],
        )

    def test_uses_explicit_review_config_and_parses_string_issue_states(self) -> None:
        config = types.SimpleNamespace(
            base_url="https://jira.example",
            token="jira-token",
            project="PROJ",
            assignee="me",
            issue_states="To Do, In Progress",
            progress_state_field='status',
            progress_state='In Progress',
            review_state_field='status',
            review_state='Code Review',
        )
        client = Mock()

        task_state_service = TaskStateService(config, TaskDataAccess(config, client))
        task_state_service.move_task_to_in_progress('PROJ-1')
        task_state_service.move_task_to_review('PROJ-1')
        task_state_service.move_task_to_open('PROJ-1')

        self.assertEqual(
            client.move_issue_to_state.call_args_list,
            [
                unittest.mock.call('PROJ-1', 'status', 'In Progress'),
                unittest.mock.call('PROJ-1', 'status', 'Code Review'),
                unittest.mock.call('PROJ-1', 'status', 'To Do'),
            ],
        )

    def test_done_defaults_to_the_review_field_and_done_value(self) -> None:
        # Trackers where every workflow move writes the same field
        # (YouTrack ``State``, Jira ``status``) need no done-specific
        # config at all — done follows the review field.
        config = types.SimpleNamespace(
            base_url="https://youtrack.example",
            token="yt-token",
            project="PROJ",
            assignee="me",
            issue_states=["Todo", "Open"],
        )
        client = Mock()

        TaskStateService(config, TaskDataAccess(config, client)).move_task_to_done('PROJ-1')

        client.move_issue_to_state.assert_called_once_with('PROJ-1', 'State', 'Done')

    def test_done_uses_its_own_field_when_configured(self) -> None:
        # GitHub/GitLab: the review transition writes a LABEL, but done
        # means closing the issue — a different field entirely.
        config = types.SimpleNamespace(
            base_url="https://api.github.com",
            token="gh-token",
            project="repo",
            assignee="octocat",
            issue_states="open",
            review_state_field='labels',
            review_state='In Review',
            done_state_field='state',
            done_state='closed',
        )
        client = Mock()

        TaskStateService(config, TaskDataAccess(config, client)).move_task_to_done('7')

        client.move_issue_to_state.assert_called_once_with('7', 'state', 'closed')

    def test_done_move_failure_propagates(self) -> None:
        # The forget endpoint refuses to delete anything when the ticket
        # didn't move, so the failure must NOT be swallowed here.
        config = types.SimpleNamespace(
            base_url="https://youtrack.example",
            token="yt-token",
            project="PROJ",
            assignee="me",
            issue_states=["Todo"],
        )
        client = Mock()
        client.move_issue_to_state.side_effect = RuntimeError('workflow rejected')

        service = TaskStateService(config, TaskDataAccess(config, client))
        with self.assertRaises(RuntimeError):
            service.move_task_to_done('PROJ-1')

    def test_prefers_explicit_open_state_when_configured(self) -> None:
        config = types.SimpleNamespace(
            base_url="https://jira.example",
            token="jira-token",
            project="PROJ",
            assignee="me",
            issue_states="To Do, In Progress",
            progress_state_field='status',
            progress_state='In Progress',
            review_state_field='status',
            review_state='Code Review',
            open_state_field='status',
            open_state='Open',
        )
        client = Mock()

        task_state_service = TaskStateService(config, TaskDataAccess(config, client))
        task_state_service.move_task_to_open('PROJ-1')

        client.move_issue_to_state.assert_called_once_with(
            'PROJ-1',
            'status',
            'Open',
        )
