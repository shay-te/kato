import unittest
from unittest.mock import patch


from github_core_lib.github_core_lib.client.github_issues_client import GitHubIssuesClient
from kato_core_lib.client.ticket_client_factory import build_ticket_client
from tests.utils import build_test_cfg


class TicketClientFactoryTests(unittest.TestCase):
    def test_builds_youtrack_client_by_default(self) -> None:
        cfg = build_test_cfg()

        with patch('kato_core_lib.client.ticket_client_factory.YouTrackClient') as mock_client_cls:
            client = build_ticket_client('youtrack', cfg.kato.youtrack, 5)

        self.assertIs(client, mock_client_cls.return_value)
        mock_client_cls.assert_called_once_with(
            cfg.kato.youtrack.base_url,
            cfg.kato.youtrack.token,
            5,
        )

    def test_builds_jira_client(self) -> None:
        cfg = build_test_cfg()

        with patch('kato_core_lib.client.ticket_client_factory.JiraCoreLib') as mock_core_lib:
            client = build_ticket_client('jira', cfg.kato.jira, 5)

        self.assertIs(client, mock_core_lib.return_value.issue)
        mock_core_lib.assert_called_once()
        passed_cfg = mock_core_lib.call_args.args[0]
        self.assertEqual(passed_cfg.core_lib.jira_core_lib.base_url, cfg.kato.jira.base_url)
        self.assertEqual(passed_cfg.core_lib.jira_core_lib.token, cfg.kato.jira.token)
        self.assertEqual(passed_cfg.core_lib.jira_core_lib.email, cfg.kato.jira.email)
        self.assertEqual(passed_cfg.core_lib.jira_core_lib.project, cfg.kato.jira.project)
        self.assertEqual(passed_cfg.core_lib.jira_core_lib.max_retries, 5)

    def test_builds_github_issues_client(self) -> None:
        cfg = build_test_cfg()
        client = build_ticket_client('github', cfg.kato.github_issues, 5)

        self.assertIsInstance(client, GitHubIssuesClient)
        self.assertEqual(client.max_retries, 5)

    def test_builds_gitlab_issues_client(self) -> None:
        cfg = build_test_cfg()

        with patch('kato_core_lib.client.ticket_client_factory.GitLabCoreLib') as mock_core_lib:
            client = build_ticket_client('gitlab', cfg.kato.gitlab_issues, 5)

        self.assertIs(client, mock_core_lib.return_value.issue)
        mock_core_lib.assert_called_once()
        passed_cfg = mock_core_lib.call_args.args[0]
        self.assertEqual(passed_cfg.core_lib.gitlab_core_lib.base_url, cfg.kato.gitlab_issues.base_url)
        self.assertEqual(passed_cfg.core_lib.gitlab_core_lib.token, cfg.kato.gitlab_issues.token)
        self.assertEqual(passed_cfg.core_lib.gitlab_core_lib.project, cfg.kato.gitlab_issues.project)
        self.assertEqual(passed_cfg.core_lib.gitlab_core_lib.max_retries, 5)

    def test_builds_bitbucket_issues_client(self) -> None:
        cfg = build_test_cfg()

        with patch('kato_core_lib.client.ticket_client_factory.BitbucketCoreLib') as mock_core_lib:
            client = build_ticket_client('bitbucket', cfg.kato.bitbucket_issues, 5)

        self.assertIs(client, mock_core_lib.return_value.issue)
        mock_core_lib.assert_called_once()
        passed_cfg = mock_core_lib.call_args.args[0]
        self.assertEqual(passed_cfg.core_lib.bitbucket_core_lib.base_url, cfg.kato.bitbucket_issues.base_url)
        self.assertEqual(passed_cfg.core_lib.bitbucket_core_lib.workspace, cfg.kato.bitbucket_issues.workspace)
        self.assertEqual(passed_cfg.core_lib.bitbucket_core_lib.max_retries, 5)

    def test_builds_bitbucket_issues_client_with_username(self) -> None:
        cfg = build_test_cfg()
        cfg.kato.bitbucket_issues.username = 'bb-user'

        with patch('kato_core_lib.client.ticket_client_factory.BitbucketCoreLib') as mock_core_lib:
            client = build_ticket_client('bitbucket', cfg.kato.bitbucket_issues, 5)

        self.assertIs(client, mock_core_lib.return_value.issue)
        mock_core_lib.assert_called_once()
        passed_cfg = mock_core_lib.call_args.args[0]
        self.assertEqual(passed_cfg.core_lib.bitbucket_core_lib.username, 'bb-user')

    def test_rejects_unknown_issue_platform(self) -> None:
        cfg = build_test_cfg()

        with self.assertRaisesRegex(ValueError, 'unsupported issue platform'):
            build_ticket_client('linear', cfg.kato.youtrack, 5)
