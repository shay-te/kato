from __future__ import annotations

import unittest
from unittest.mock import patch

from omegaconf import OmegaConf

from task_core_lib.task_core_lib.task_core_lib import TaskCoreLib


class TaskCoreLibTests(unittest.TestCase):
    def test_exposes_youtrack_issue_provider(self) -> None:
        cfg = OmegaConf.create(
            {
                'base_url': 'https://your-company.youtrack.cloud',
                'token': 'yt-token',
                'project': 'PROJ',
                'assignee': 'me',
            }
        )

        with patch(
            'task_core_lib.task_core_lib.task_core_lib.TaskClientFactory',
        ) as mock_factory_class:
            factory = mock_factory_class.return_value
            issue = factory.get.return_value

            core_lib = TaskCoreLib('youtrack', cfg, 4)

        self.assertIs(core_lib.issue, issue)
        mock_factory_class.assert_called_once_with(cfg, 4)
        factory.get.assert_called_once_with('youtrack')

    def test_exposes_jira_issue_provider(self) -> None:
        cfg = OmegaConf.create(
            {
                'base_url': 'https://your-company.atlassian.net',
                'token': 'jira-token',
                'email': 'me@example.com',
                'project': 'PROJ',
            }
        )

        with patch(
            'task_core_lib.task_core_lib.task_core_lib.TaskClientFactory',
        ) as mock_factory_class:
            factory = mock_factory_class.return_value
            issue = factory.get.return_value

            core_lib = TaskCoreLib('jira', cfg, 4)

        self.assertIs(core_lib.issue, issue)
        mock_factory_class.assert_called_once_with(cfg, 4)
        factory.get.assert_called_once_with('jira')

    def test_exposes_bitbucket_issue_provider(self) -> None:
        cfg = OmegaConf.create(
            {
                'base_url': 'https://api.bitbucket.org/2.0',
                'token': 'bb-token',
                'username': 'bb-user',
                'workspace': 'workspace',
                'repo_slug': 'repo',
            }
        )

        with patch(
            'task_core_lib.task_core_lib.task_core_lib.TaskClientFactory',
        ) as mock_factory_class:
            factory = mock_factory_class.return_value
            issue = factory.get.return_value

            core_lib = TaskCoreLib('bitbucket', cfg, 4)

        self.assertIs(core_lib.issue, issue)
        mock_factory_class.assert_called_once_with(cfg, 4)
        factory.get.assert_called_once_with('bitbucket')
