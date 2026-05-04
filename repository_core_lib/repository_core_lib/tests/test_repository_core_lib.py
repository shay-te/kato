from __future__ import annotations

import unittest
from unittest.mock import patch

from omegaconf import OmegaConf

from repository_core_lib.repository_core_lib.repository_core_lib import RepositoryCoreLib


class RepositoryCoreLibTests(unittest.TestCase):
    def test_exposes_github_pull_request_service(self) -> None:
        cfg = OmegaConf.create(
            {
                'base_url': 'https://api.github.com',
                'token': 'gh-token',
                'owner': 'octo',
                'repo_slug': 'repo',
            }
        )

        with patch(
            'repository_core_lib.repository_core_lib.repository_core_lib.PullRequestClientFactory',
        ) as mock_factory_class, patch(
            'repository_core_lib.repository_core_lib.repository_core_lib.PullRequestService',
        ) as mock_service_class:
            factory = mock_factory_class.return_value
            service = mock_service_class.return_value

            core_lib = RepositoryCoreLib(cfg, 4)

        self.assertIs(core_lib.pull_request, service)
        mock_factory_class.assert_called_once_with(cfg, 4)
        mock_service_class.assert_called_once_with(factory)

    def test_exposes_gitlab_pull_request_service(self) -> None:
        cfg = OmegaConf.create(
            {
                'base_url': 'https://gitlab.example/api/v4',
                'token': 'gl-token',
                'project': 'group/repo',
            }
        )

        with patch(
            'repository_core_lib.repository_core_lib.repository_core_lib.PullRequestClientFactory',
        ) as mock_factory_class, patch(
            'repository_core_lib.repository_core_lib.repository_core_lib.PullRequestService',
        ) as mock_service_class:
            factory = mock_factory_class.return_value
            service = mock_service_class.return_value

            core_lib = RepositoryCoreLib(cfg, 4)

        self.assertIs(core_lib.pull_request, service)
        mock_factory_class.assert_called_once_with(cfg, 4)
        mock_service_class.assert_called_once_with(factory)

    def test_exposes_bitbucket_pull_request_service(self) -> None:
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
            'repository_core_lib.repository_core_lib.repository_core_lib.PullRequestClientFactory',
        ) as mock_factory_class, patch(
            'repository_core_lib.repository_core_lib.repository_core_lib.PullRequestService',
        ) as mock_service_class:
            factory = mock_factory_class.return_value
            service = mock_service_class.return_value

            core_lib = RepositoryCoreLib(cfg, 4)

        self.assertIs(core_lib.pull_request, service)
        mock_factory_class.assert_called_once_with(cfg, 4)
        mock_service_class.assert_called_once_with(factory)
