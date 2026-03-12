import types
import unittest
from unittest.mock import Mock, patch

import bootstrap  # noqa: F401

from openhands_agent.data_layers.data_access.pull_request_data_access import (
    PullRequestDataAccess,
)
from openhands_agent.data_layers.data_access.task_data_access import TaskDataAccess
from openhands_agent.data_layers.service.agent_service import AgentService
from openhands_agent.data_layers.service.implementation_service import (
    ImplementationService,
)
from openhands_agent.data_layers.service.notification_service import NotificationService
from openhands_agent.fields import EmailFields, ImplementationFields, PullRequestFields, StatusFields
from utils import build_review_comment_payload, build_task, build_test_cfg


class AgentServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = build_test_cfg()
        task_client = types.SimpleNamespace(
            get_assigned_tasks=Mock(
                return_value=[
                    build_task(),
                    build_task(
                        task_id='PROJ-2',
                        summary='Skip bug',
                        branch_name='feature/proj-2',
                    ),
                ]
            ),
            add_pull_request_comment=Mock(),
            move_issue_to_state=Mock(),
        )
        self.task_data_access = TaskDataAccess(
            self.cfg.openhands_agent.youtrack,
            task_client,
        )
        self.task_client = task_client
        self.openhands_client = types.SimpleNamespace(
            implement_task=Mock(
                side_effect=[
                    {
                        ImplementationFields.SUCCESS: True,
                        "branch_name": "feature/proj-1",
                        "summary": "Implemented PROJ-1",
                    },
                    {
                        ImplementationFields.SUCCESS: False,
                        "branch_name": "feature/proj-2",
                        "summary": "Failed PROJ-2",
                    },
                ]
            ),
            fix_review_comment=Mock(return_value={ImplementationFields.SUCCESS: True}),
        )
        self.implementation_service = ImplementationService(self.openhands_client)
        pull_request_client = types.SimpleNamespace(
            create_pull_request=Mock(
                return_value={
                    PullRequestFields.ID: "17",
                    PullRequestFields.TITLE: "PROJ-1: Fix bug",
                    PullRequestFields.URL: "https://bitbucket/pr/17",
                }
            )
        )
        self.pull_request_client = pull_request_client
        self.pull_request_data_access = PullRequestDataAccess(
            self.cfg.openhands_agent.bitbucket,
            pull_request_client,
        )
        self.email_core_lib = Mock()
        self.notification_service = NotificationService(
            app_name=self.cfg.core_lib.app.name,
            email_core_lib=self.email_core_lib,
            failure_email_cfg=self.cfg.openhands_agent.failure_email,
            completion_email_cfg=self.cfg.openhands_agent.completion_email,
        )
        self.service = AgentService(
            self.task_data_access,
            self.implementation_service,
            self.pull_request_data_access,
            self.notification_service,
        )

    def test_init_rejects_missing_notification_service(self) -> None:
        with self.assertRaisesRegex(ValueError, 'notification_service is required'):
            AgentService(
                self.task_data_access,
                self.implementation_service,
                self.pull_request_data_access,
                None,
            )

    def test_validate_connections_checks_all_dependencies(self) -> None:
        self.task_client.validate_connection = Mock()
        self.openhands_client.validate_connection = Mock()
        self.pull_request_client.validate_connection = Mock()

        self.service.validate_connections()

        self.task_client.validate_connection.assert_called_once_with(
            project='PROJ',
            assignee='me',
            states=['Todo', 'Open'],
        )
        self.openhands_client.validate_connection.assert_called_once_with()
        self.pull_request_client.validate_connection.assert_called_once_with(
            workspace='workspace',
            repo_slug='repo',
        )

    def test_validate_connections_raises_with_service_stack_traces(self) -> None:
        self.task_client.validate_connection = Mock(side_effect=RuntimeError('youtrack down'))
        self.openhands_client.validate_connection = Mock(side_effect=RuntimeError('openhands down'))
        self.pull_request_client.validate_connection = Mock()
        self.service.logger = Mock()

        with self.assertRaisesRegex(RuntimeError, 'startup dependency validation failed') as exc_context:
            self.service.validate_connections()

        self.assertEqual(self.service.logger.exception.call_count, 2)
        self.assertIn('[youtrack]', str(exc_context.exception))
        self.assertIn('RuntimeError: youtrack down', str(exc_context.exception))
        self.assertIn('[openhands]', str(exc_context.exception))
        self.assertIn('RuntimeError: openhands down', str(exc_context.exception))

    def test_process_assigned_tasks_creates_pull_requests_for_successful_tasks(self) -> None:
        results = self.service.process_assigned_tasks()

        self.assertEqual(
            results,
            [
                {
                    PullRequestFields.ID: "17",
                    PullRequestFields.TITLE: "PROJ-1: Fix bug",
                    PullRequestFields.URL: "https://bitbucket/pr/17",
                }
            ],
        )
        self.pull_request_client.create_pull_request.assert_called_once_with(
            title="PROJ-1: Fix bug",
            source_branch="feature/proj-1",
            workspace="workspace",
            repo_slug="repo",
            destination_branch="main",
            description="Implemented PROJ-1",
        )
        self.task_client.add_pull_request_comment.assert_called_once_with(
            "PROJ-1",
            "https://bitbucket/pr/17",
        )
        self.task_client.move_issue_to_state.assert_called_once_with("PROJ-1", "State", "In Review")
        self.assertEqual(self.email_core_lib.send.call_count, 2)
        completion_email_call = self.email_core_lib.send.call_args_list[0]
        self.assertEqual(completion_email_call.args[0], '77')
        self.assertEqual(completion_email_call.args[1][EmailFields.EMAIL], 'reviewers@example.com')
        self.assertEqual(completion_email_call.args[1][EmailFields.TASK_ID], 'PROJ-1')
        self.assertEqual(
            completion_email_call.args[1][EmailFields.PULL_REQUEST_URL],
            'https://bitbucket/pr/17',
        )
        self.assertEqual(self.service._pull_request_branch_map, {"17": "feature/proj-1"})

    def test_handle_pull_request_comment_updates_known_branch(self) -> None:
        self.service._pull_request_branch_map["17"] = "feature/proj-1"
        payload = build_review_comment_payload()

        result = self.service.handle_pull_request_comment(payload)

        self.assertEqual(
            result,
            {
                StatusFields.STATUS: StatusFields.UPDATED,
                "pull_request_id": "17",
                "branch_name": "feature/proj-1",
            },
        )
        self.openhands_client.fix_review_comment.assert_called_once()
        comment_arg = self.openhands_client.fix_review_comment.call_args.args[0]
        self.assertEqual(comment_arg.pull_request_id, "17")
        self.assertEqual(comment_arg.comment_id, "99")
        self.assertEqual(comment_arg.author, "reviewer")
        self.assertEqual(comment_arg.body, "Please rename this variable.")

    def test_handle_pull_request_comment_rejects_invalid_payload(self) -> None:
        with self.assertRaisesRegex(ValueError, 'invalid review comment payload'):
            self.service.handle_pull_request_comment({"pull_request_id": "17"})

    def test_handle_pull_request_comment_rejects_unknown_pull_request(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown pull request id"):
            self.service.handle_pull_request_comment(build_review_comment_payload())

    def test_handle_pull_request_comment_raises_when_fix_fails(self) -> None:
        self.openhands_client.fix_review_comment.return_value = {ImplementationFields.SUCCESS: False}
        self.service._pull_request_branch_map["17"] = "feature/proj-1"

        with self.assertRaisesRegex(RuntimeError, "failed to address comment 99"):
            self.service.handle_pull_request_comment(build_review_comment_payload())

    def test_process_assigned_tasks_returns_empty_when_no_tasks_exist(self) -> None:
        self.task_client.get_assigned_tasks.return_value = []

        results = self.service.process_assigned_tasks()

        self.assertEqual(results, [])
        self.openhands_client.implement_task.assert_not_called()
        self.task_client.move_issue_to_state.assert_not_called()
        self.email_core_lib.send.assert_not_called()

    def test_process_assigned_tasks_uses_task_branch_when_execution_payload_is_partial(self) -> None:
        self.task_client.get_assigned_tasks.return_value = [build_task()]
        self.openhands_client.implement_task.side_effect = None
        self.openhands_client.implement_task.return_value = {
            ImplementationFields.SUCCESS: True,
        }

        results = self.service.process_assigned_tasks()

        self.assertEqual(results[0][PullRequestFields.ID], '17')
        self.pull_request_client.create_pull_request.assert_called_once_with(
            title='PROJ-1: Fix bug',
            source_branch='feature/proj-1',
            workspace='workspace',
            repo_slug='repo',
            destination_branch='main',
            description='',
        )
        self.task_client.move_issue_to_state.assert_called_once_with(
            'PROJ-1',
            'State',
            'In Review',
        )

    def test_process_assigned_tasks_skips_execution_without_success_flag(self) -> None:
        self.task_client.get_assigned_tasks.return_value = [build_task()]
        self.openhands_client.implement_task.side_effect = None
        self.openhands_client.implement_task.return_value = {}

        self.service.logger = Mock()
        with patch.object(self.service, 'logger', self.service.logger):
            results = self.service.process_assigned_tasks()

        self.assertEqual(results, [])
        self.pull_request_client.create_pull_request.assert_not_called()
        self.task_client.move_issue_to_state.assert_not_called()
        self.email_core_lib.send.assert_not_called()
        self.service.logger.warning.assert_called_once_with(
            'implementation failed for task %s',
            'PROJ-1',
        )

    def test_process_assigned_tasks_raises_when_move_to_review_fails(self) -> None:
        self.task_client.get_assigned_tasks.return_value = [build_task()]
        self.openhands_client.implement_task.side_effect = None
        self.openhands_client.implement_task.return_value = {
            ImplementationFields.SUCCESS: True,
        }
        self.task_client.move_issue_to_state.side_effect = RuntimeError('state update failed')

        with self.assertRaisesRegex(RuntimeError, 'state update failed'):
            self.service.process_assigned_tasks()

        self.email_core_lib.send.assert_not_called()

    def test_process_assigned_tasks_ignores_completion_notification_failures(self) -> None:
        self.task_client.get_assigned_tasks.return_value = [build_task()]
        self.openhands_client.implement_task.side_effect = None
        self.openhands_client.implement_task.return_value = {
            ImplementationFields.SUCCESS: True,
        }
        self.notification_service.notify_task_ready_for_review = Mock(
            side_effect=RuntimeError('smtp failed')
        )

        self.service.logger = Mock()
        with patch.object(self.service, 'logger', self.service.logger):
            results = self.service.process_assigned_tasks()

        self.assertEqual(results[0][PullRequestFields.ID], '17')
        self.task_client.move_issue_to_state.assert_called_once_with(
            'PROJ-1',
            'State',
            'In Review',
        )
        self.service.logger.exception.assert_called_once_with(
            'failed to send completion notification for task %s',
            'PROJ-1',
        )

    def test_process_assigned_tasks_processes_multiple_successful_tasks(self) -> None:
        self.task_client.get_assigned_tasks.return_value = [
            build_task(),
            build_task(
                task_id='PROJ-2',
                summary='Second bug',
                branch_name='feature/proj-2',
            ),
        ]
        self.openhands_client.implement_task.side_effect = [
            {
                ImplementationFields.SUCCESS: True,
                "branch_name": "feature/proj-1",
                "summary": "Implemented PROJ-1",
            },
            {
                ImplementationFields.SUCCESS: True,
                "branch_name": "feature/proj-2",
                "summary": "Implemented PROJ-2",
            },
        ]
        self.pull_request_client.create_pull_request.side_effect = [
            {
                PullRequestFields.ID: "17",
                PullRequestFields.TITLE: "PROJ-1: Fix bug",
                PullRequestFields.URL: "https://bitbucket/pr/17",
            },
            {
                PullRequestFields.ID: "18",
                PullRequestFields.TITLE: "PROJ-2: Second bug",
                PullRequestFields.URL: "https://bitbucket/pr/18",
            },
        ]

        results = self.service.process_assigned_tasks()

        self.assertEqual(
            results,
            [
                {
                    PullRequestFields.ID: "17",
                    PullRequestFields.TITLE: "PROJ-1: Fix bug",
                    PullRequestFields.URL: "https://bitbucket/pr/17",
                },
                {
                    PullRequestFields.ID: "18",
                    PullRequestFields.TITLE: "PROJ-2: Second bug",
                    PullRequestFields.URL: "https://bitbucket/pr/18",
                },
            ],
        )
        self.assertEqual(
            self.service._pull_request_branch_map,
            {"17": "feature/proj-1", "18": "feature/proj-2"},
        )
        self.assertEqual(self.task_client.add_pull_request_comment.call_count, 2)
        self.assertEqual(self.task_client.move_issue_to_state.call_count, 2)
        self.assertEqual(self.email_core_lib.send.call_count, 4)
