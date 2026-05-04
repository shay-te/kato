from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from omegaconf import OmegaConf

from core_lib.error_handling.status_code_exception import StatusCodeException
from task_core_lib.task_core_lib.client.task_client_factory import TaskClientFactory
from task_core_lib.task_core_lib.platform import Platform
from vcs_provider_contracts.vcs_provider_contracts.issue_provider import IssueProvider


class TaskClientFactoryTests(unittest.TestCase):
    def test_builds_youtrack_client_by_default(self) -> None:
        cfg = OmegaConf.create(
            {
                'base_url': 'https://your-company.youtrack.cloud',
                'token': 'yt-token',
                'project': 'PROJ',
                'assignee': 'me',
            }
        )

        with patch(
            'task_core_lib.task_core_lib.client.task_client_factory.YouTrackCoreLib',
        ) as mock_core_lib:
            client = TaskClientFactory(cfg, 5).get(Platform.YOUTRACK)

        self.assertIs(client, mock_core_lib.return_value.issue)
        mock_core_lib.assert_called_once()
        passed_cfg = mock_core_lib.call_args.args[0]
        self.assertEqual(passed_cfg.core_lib.youtrack_core_lib.base_url, cfg.base_url)
        self.assertEqual(passed_cfg.core_lib.youtrack_core_lib.token, cfg.token)
        self.assertEqual(passed_cfg.core_lib.youtrack_core_lib.project, cfg.project)
        self.assertEqual(passed_cfg.core_lib.youtrack_core_lib.assignee, cfg.assignee)
        self.assertEqual(passed_cfg.core_lib.youtrack_core_lib.max_retries, 5)

    def test_builds_jira_client(self) -> None:
        cfg = OmegaConf.create(
            {
                'base_url': 'https://your-company.atlassian.net',
                'token': 'jira-token',
                'email': 'me@example.com',
                'project': 'PROJ',
            }
        )

        with patch(
            'task_core_lib.task_core_lib.client.task_client_factory.JiraCoreLib',
        ) as mock_core_lib:
            client = TaskClientFactory(cfg, 5).get(Platform.JIRA)

        self.assertIs(client, mock_core_lib.return_value.issue)
        mock_core_lib.assert_called_once()
        passed_cfg = mock_core_lib.call_args.args[0]
        self.assertEqual(passed_cfg.core_lib.jira_core_lib.base_url, cfg.base_url)
        self.assertEqual(passed_cfg.core_lib.jira_core_lib.token, cfg.token)
        self.assertEqual(passed_cfg.core_lib.jira_core_lib.email, cfg.email)
        self.assertEqual(passed_cfg.core_lib.jira_core_lib.project, cfg.project)
        self.assertEqual(passed_cfg.core_lib.jira_core_lib.max_retries, 5)

    def test_builds_github_issues_client(self) -> None:
        cfg = OmegaConf.create(
            {
                'base_url': 'https://api.github.com',
                'token': 'gh-token',
                'owner': 'octo',
                'repo_slug': 'repo',
            }
        )

        with patch(
            'task_core_lib.task_core_lib.client.task_client_factory.GitHubCoreLib',
        ) as mock_core_lib:
            client = TaskClientFactory(cfg, 5).get(Platform.GITHUB)

        self.assertIs(client, mock_core_lib.return_value.issue)
        mock_core_lib.assert_called_once()
        passed_cfg = mock_core_lib.call_args.args[0]
        self.assertEqual(passed_cfg.core_lib.github_core_lib.base_url, cfg.base_url)
        self.assertEqual(passed_cfg.core_lib.github_core_lib.token, cfg.token)
        self.assertEqual(passed_cfg.core_lib.github_core_lib.owner, cfg.owner)
        self.assertEqual(passed_cfg.core_lib.github_core_lib.repo_slug, cfg.repo_slug)
        self.assertEqual(passed_cfg.core_lib.github_core_lib.max_retries, 5)

    def test_builds_gitlab_issues_client(self) -> None:
        cfg = OmegaConf.create(
            {
                'base_url': 'https://gitlab.example/api/v4',
                'token': 'gl-token',
                'project': 'group/repo',
            }
        )

        with patch(
            'task_core_lib.task_core_lib.client.task_client_factory.GitLabCoreLib',
        ) as mock_core_lib:
            client = TaskClientFactory(cfg, 5).get(Platform.GITLAB)

        self.assertIs(client, mock_core_lib.return_value.issue)
        mock_core_lib.assert_called_once()
        passed_cfg = mock_core_lib.call_args.args[0]
        self.assertEqual(passed_cfg.core_lib.gitlab_core_lib.base_url, cfg.base_url)
        self.assertEqual(passed_cfg.core_lib.gitlab_core_lib.token, cfg.token)
        self.assertEqual(passed_cfg.core_lib.gitlab_core_lib.project, cfg.project)
        self.assertEqual(passed_cfg.core_lib.gitlab_core_lib.max_retries, 5)

    def test_builds_bitbucket_issues_client(self) -> None:
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
            'task_core_lib.task_core_lib.client.task_client_factory.BitbucketCoreLib',
        ) as mock_core_lib:
            client = TaskClientFactory(cfg, 5).get(Platform.BITBUCKET)

        self.assertIs(client, mock_core_lib.return_value.issue)
        mock_core_lib.assert_called_once()
        passed_cfg = mock_core_lib.call_args.args[0]
        self.assertEqual(passed_cfg.core_lib.bitbucket_core_lib.base_url, cfg.base_url)
        self.assertEqual(passed_cfg.core_lib.bitbucket_core_lib.workspace, cfg.workspace)
        self.assertEqual(passed_cfg.core_lib.bitbucket_core_lib.repo_slug, cfg.repo_slug)
        self.assertEqual(passed_cfg.core_lib.bitbucket_core_lib.max_retries, 5)

    def test_builds_bitbucket_issues_client_with_username(self) -> None:
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
            'task_core_lib.task_core_lib.client.task_client_factory.BitbucketCoreLib',
        ) as mock_core_lib:
            client = TaskClientFactory(cfg, 5).get(Platform.BITBUCKET)

        self.assertIs(client, mock_core_lib.return_value.issue)
        passed_cfg = mock_core_lib.call_args.args[0]
        self.assertEqual(passed_cfg.core_lib.bitbucket_core_lib.username, 'bb-user')

    def test_accepts_issue_platform_aliases(self) -> None:
        cfg = OmegaConf.create(
            {
                'base_url': 'https://api.github.com',
                'token': 'gh-token',
                'owner': 'octo',
                'repo_slug': 'repo',
            }
        )
        factory = TaskClientFactory(cfg, 5)

        with patch(
            'task_core_lib.task_core_lib.client.task_client_factory.GitHubCoreLib',
        ) as mock_core_lib:
            self.assertIs(factory.get(Platform.GITHUB_ISSUES), mock_core_lib.return_value.issue)
            self.assertIs(factory.get(Platform.GITHUB), mock_core_lib.return_value.issue)

        self.assertEqual(mock_core_lib.call_count, 2)

    def test_raw_get_returns_none_for_unsupported_issue_platform(self) -> None:
        cfg = OmegaConf.create(
            {
                'base_url': 'https://your-company.youtrack.cloud',
                'token': 'yt-token',
                'project': 'PROJ',
                'assignee': 'me',
            }
        )
        factory = TaskClientFactory(cfg, 5)

        result = TaskClientFactory.get.__wrapped__(factory, Mock())

        self.assertIsNone(result)

    def test_supported_get_returns_issue_provider(self) -> None:
        cfg = OmegaConf.create(
            {
                'base_url': 'https://your-company.youtrack.cloud',
                'token': 'yt-token',
                'project': 'PROJ',
                'assignee': 'me',
            }
        )

        with patch(
            'task_core_lib.task_core_lib.client.task_client_factory.YouTrackCoreLib',
        ) as mock_core_lib:
            issue = TaskClientFactory(cfg, 5).get(Platform.YOUTRACK)

        self.assertIsInstance(issue, IssueProvider)
        mock_core_lib.assert_called_once()
