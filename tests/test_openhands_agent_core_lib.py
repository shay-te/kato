import types
import unittest
from unittest.mock import patch

import bootstrap  # noqa: F401

from openhands_agent.openhands_agent_core_lib import OpenHandsAgentCoreLib


def _build_cfg() -> types.SimpleNamespace:
    return types.SimpleNamespace(
        openhands_agent=types.SimpleNamespace(
            youtrack=types.SimpleNamespace(
                name='youtrack-config',
                base_url='https://youtrack.example',
                token='yt-token',
            ),
            openhands=types.SimpleNamespace(
                name='openhands-config',
                base_url='https://openhands.example',
                api_key='oh-token',
            ),
            bitbucket=types.SimpleNamespace(
                name='bitbucket-config',
                base_url='https://bitbucket.example',
                token='bb-token',
            ),
        )
    )


class OpenHandsAgentCoreLibTests(unittest.TestCase):
    def test_builds_data_access_and_service_in_core_lib(self) -> None:
        cfg = _build_cfg()

        with patch(
            'openhands_agent.openhands_agent_core_lib.YouTrackClient'
        ) as mock_youtrack_client_cls, patch(
            'openhands_agent.openhands_agent_core_lib.OpenHandsClient'
        ) as mock_openhands_client_cls, patch(
            'openhands_agent.openhands_agent_core_lib.BitbucketClient'
        ) as mock_bitbucket_client_cls, patch(
            'openhands_agent.openhands_agent_core_lib.TaskDataAccess'
        ) as mock_task_da_cls, patch(
            'openhands_agent.openhands_agent_core_lib.ImplementationDataAccess'
        ) as mock_impl_da_cls, patch(
            'openhands_agent.openhands_agent_core_lib.PullRequestDataAccess'
        ) as mock_pr_da_cls, patch(
            'openhands_agent.openhands_agent_core_lib.AgentService'
        ) as mock_service_cls:
            app = OpenHandsAgentCoreLib(cfg)

        mock_youtrack_client_cls.assert_called_once_with(cfg.openhands_agent.youtrack.base_url, cfg.openhands_agent.youtrack.token)
        mock_openhands_client_cls.assert_called_once_with(cfg.openhands_agent.openhands.base_url, cfg.openhands_agent.openhands.api_key)
        mock_bitbucket_client_cls.assert_called_once_with(cfg.openhands_agent.bitbucket.base_url, cfg.openhands_agent.bitbucket.token)
        mock_task_da_cls.assert_called_once_with(cfg.openhands_agent.youtrack, mock_youtrack_client_cls.return_value)
        mock_impl_da_cls.assert_called_once_with(cfg.openhands_agent.openhands, mock_openhands_client_cls.return_value)
        mock_pr_da_cls.assert_called_once_with(cfg.openhands_agent.bitbucket, mock_bitbucket_client_cls.return_value)
        mock_service_cls.assert_called_once_with(
            task_data_access=mock_task_da_cls.return_value,
            implementation_data_access=mock_impl_da_cls.return_value,
            pull_request_data_access=mock_pr_da_cls.return_value,
        )
        self.assertIs(app.agent_service, mock_service_cls.return_value)
        self.assertIs(app._task_data_access, mock_task_da_cls.return_value)
        self.assertIs(app._implementation_data_access, mock_impl_da_cls.return_value)
        self.assertIs(app._pull_request_data_access, mock_pr_da_cls.return_value)

    def test_delegates_process_assigned_tasks_to_service(self) -> None:
        cfg = _build_cfg()

        with patch(
            'openhands_agent.openhands_agent_core_lib.YouTrackClient'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.OpenHandsClient'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.BitbucketClient'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.TaskDataAccess'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.ImplementationDataAccess'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.PullRequestDataAccess'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.AgentService'
        ) as mock_service_cls:
            mock_service_cls.return_value.process_assigned_tasks.return_value = [{"id": "17"}]
            app = OpenHandsAgentCoreLib(cfg)

        self.assertEqual(app.process_assigned_tasks(), [{"id": "17"}])
        mock_service_cls.return_value.process_assigned_tasks.assert_called_once_with()

    def test_delegates_comment_handling_to_service(self) -> None:
        cfg = _build_cfg()
        payload = {"pull_request_id": "17"}

        with patch(
            'openhands_agent.openhands_agent_core_lib.YouTrackClient'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.OpenHandsClient'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.BitbucketClient'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.TaskDataAccess'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.ImplementationDataAccess'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.PullRequestDataAccess'
        ), patch(
            'openhands_agent.openhands_agent_core_lib.AgentService'
        ) as mock_service_cls:
            mock_service_cls.return_value.handle_pull_request_comment.return_value = {"status": "updated"}
            app = OpenHandsAgentCoreLib(cfg)

        self.assertEqual(app.handle_pull_request_comment(payload), {"status": "updated"})
        mock_service_cls.return_value.handle_pull_request_comment.assert_called_once_with(payload)
