from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from omegaconf import OmegaConf

from repository_core_lib.repository_core_lib.client.pull_request_client_factory import (
    PullRequestClientFactory,
)
from repository_core_lib.repository_core_lib.repository_type import RepositoryType
from core_lib.error_handling.status_code_exception import StatusCodeException


class PullRequestClientFactoryTests(unittest.TestCase):
    def test_get_builds_github_client(self) -> None:
        cfg = OmegaConf.create(
            {
                'base_url': 'https://api.github.com',
                'token': 'gh-token',
                'owner': 'octo',
                'repo_slug': 'repo',
            }
        )
        client = Mock()
        with patch(
            'repository_core_lib.repository_core_lib.client.pull_request_client_factory.GitHubCoreLib',
        ) as mock_github_core_lib:
            mock_github_core_lib.return_value.pull_request = client
            factory = PullRequestClientFactory(cfg, 3)

            result = factory.get(RepositoryType.GITHUB)

        self.assertIs(result, client)
        mock_github_core_lib.assert_called_once()

    def test_get_builds_gitlab_client(self) -> None:
        cfg = OmegaConf.create(
            {
                'base_url': 'https://gitlab.example/api/v4',
                'token': 'gl-token',
                'project': 'group/repo',
            }
        )
        client = Mock()
        with patch(
            'repository_core_lib.repository_core_lib.client.pull_request_client_factory.GitLabCoreLib',
        ) as mock_gitlab_core_lib:
            mock_gitlab_core_lib.return_value.pull_request = client
            factory = PullRequestClientFactory(cfg, 3)

            result = factory.get(RepositoryType.GITLAB)

        self.assertIs(result, client)
        mock_gitlab_core_lib.assert_called_once()

    def test_get_builds_bitbucket_client(self) -> None:
        cfg = OmegaConf.create(
            {
                'base_url': 'https://api.bitbucket.org/2.0',
                'token': 'bb-token',
                'username': 'bb-user',
                'workspace': 'workspace',
                'repo_slug': 'repo',
            }
        )
        client = Mock()
        with patch(
            'repository_core_lib.repository_core_lib.client.pull_request_client_factory.BitbucketCoreLib',
        ) as mock_bitbucket_core_lib:
            mock_bitbucket_core_lib.return_value.pull_request = client
            factory = PullRequestClientFactory(cfg, 3)

            result = factory.get(RepositoryType.BITBUCKET)

        self.assertIs(result, client)
        mock_bitbucket_core_lib.assert_called_once()

    def test_rejects_unsupported_repository_type(self) -> None:
        cfg = OmegaConf.create(
            {
                'base_url': 'https://api.github.com',
                'token': 'gh-token',
                'owner': 'octo',
                'repo_slug': 'repo',
            }
        )
        factory = PullRequestClientFactory(cfg, 3)

        with self.assertRaisesRegex(StatusCodeException, 'unsupported repository provider') as ctx:
            factory.get(Mock())

        self.assertEqual(ctx.exception.status_code, 404)

    def test_raw_get_returns_none_for_unsupported_repository_type(self) -> None:
        cfg = OmegaConf.create(
            {
                'base_url': 'https://api.github.com',
                'token': 'gh-token',
                'owner': 'octo',
                'repo_slug': 'repo',
            }
        )
        factory = PullRequestClientFactory(cfg, 3)

        result = PullRequestClientFactory.get.__wrapped__(factory, Mock())

        self.assertIsNone(result)
