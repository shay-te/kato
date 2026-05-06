from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from omegaconf import OmegaConf

from repository_core_lib.repository_core_lib.repository_core_lib import RepositoryCoreLib
from repository_core_lib.repository_core_lib.platform import Platform
from repository_core_lib.repository_core_lib.pull_request_service import PullRequestService


class RepositoryCoreLibInitTests(unittest.TestCase):
    def _make_core_lib(self, platform='github', max_retries=3, **extra_kwargs):
        configs = {
            'github': {
                'base_url': 'https://api.github.com',
                'token': 'gh-token',
                'owner': 'octo',
                'repo_slug': 'repo',
            },
            'gitlab': {
                'base_url': 'https://gitlab.example/api/v4',
                'token': 'gl-token',
                'project': 'group/repo',
            },
            'bitbucket': {
                'base_url': 'https://api.bitbucket.org/2.0',
                'token': 'bb-token',
                'username': 'bb-user',
                'workspace': 'workspace',
                'repo_slug': 'repo',
            },
        }
        cfg = OmegaConf.create(configs[platform])
        return RepositoryCoreLib(cfg, max_retries, **extra_kwargs)

    def test_exposes_pull_request_service(self):
        core_lib = self._make_core_lib()
        self.assertIsInstance(core_lib.pull_request, PullRequestService)

    def test_pull_request_is_service_instance_for_github(self):
        core_lib = self._make_core_lib('github')
        self.assertIsInstance(core_lib.pull_request, PullRequestService)

    def test_pull_request_is_service_instance_for_gitlab(self):
        core_lib = self._make_core_lib('gitlab')
        self.assertIsInstance(core_lib.pull_request, PullRequestService)

    def test_pull_request_is_service_instance_for_bitbucket(self):
        core_lib = self._make_core_lib('bitbucket')
        self.assertIsInstance(core_lib.pull_request, PullRequestService)

    def test_max_retries_is_threaded_through(self):
        client = Mock()
        factory = Mock(return_value=client)
        core_lib = self._make_core_lib('github', max_retries=7, github_client_factory=factory)
        # Trigger the factory via the service
        core_lib.pull_request.validate_connection(Platform.GITHUB, repo_owner='o', repo_slug='r')
        factory.assert_called_once()

    def test_injectable_github_factory_is_used(self):
        client = Mock()
        github_factory = Mock(return_value=client)
        core_lib = self._make_core_lib('github', github_client_factory=github_factory)
        core_lib.pull_request.validate_connection(Platform.GITHUB, repo_owner='o', repo_slug='r')
        github_factory.assert_called_once()

    def test_injectable_gitlab_factory_is_used(self):
        client = Mock()
        gitlab_factory = Mock(return_value=client)
        core_lib = self._make_core_lib('gitlab', gitlab_client_factory=gitlab_factory)
        core_lib.pull_request.validate_connection(Platform.GITLAB, repo_owner='group', repo_slug='r')
        gitlab_factory.assert_called_once()

    def test_injectable_bitbucket_factory_is_used(self):
        client = Mock()
        bitbucket_factory = Mock(return_value=client)
        core_lib = self._make_core_lib('bitbucket', bitbucket_client_factory=bitbucket_factory)
        core_lib.pull_request.validate_connection(Platform.BITBUCKET, repo_owner='ws', repo_slug='r')
        bitbucket_factory.assert_called_once()


class RepositoryCoreLibCompositionTests(unittest.TestCase):
    def test_factory_and_service_are_wired_correctly(self):
        cfg = OmegaConf.create({
            'base_url': 'https://api.github.com',
            'token': 'gh-token',
        })
        with patch(
            'repository_core_lib.repository_core_lib.repository_core_lib.PullRequestClientFactory',
        ) as mock_factory_class, patch(
            'repository_core_lib.repository_core_lib.repository_core_lib.PullRequestService',
        ) as mock_service_class:
            factory_instance = mock_factory_class.return_value
            service_instance = mock_service_class.return_value

            core_lib = RepositoryCoreLib(cfg, 4)

        self.assertIs(core_lib.pull_request, service_instance)
        mock_service_class.assert_called_once_with(factory_instance)

    def test_factory_receives_cfg_and_max_retries(self):
        cfg = OmegaConf.create({
            'base_url': 'https://api.github.com',
            'token': 'gh-token',
        })
        with patch(
            'repository_core_lib.repository_core_lib.repository_core_lib.PullRequestClientFactory',
        ) as mock_factory_class, patch(
            'repository_core_lib.repository_core_lib.repository_core_lib.PullRequestService',
        ):
            RepositoryCoreLib(cfg, 4)

        args, kwargs = mock_factory_class.call_args
        self.assertIs(args[0], cfg)
        self.assertEqual(args[1], 4)

    def test_factory_receives_none_client_factories_by_default(self):
        cfg = OmegaConf.create({
            'base_url': 'https://api.github.com',
            'token': 'gh-token',
        })
        with patch(
            'repository_core_lib.repository_core_lib.repository_core_lib.PullRequestClientFactory',
        ) as mock_factory_class, patch(
            'repository_core_lib.repository_core_lib.repository_core_lib.PullRequestService',
        ):
            RepositoryCoreLib(cfg, 3)

        _, kwargs = mock_factory_class.call_args
        self.assertIsNone(kwargs.get('github_client_factory'))
        self.assertIsNone(kwargs.get('gitlab_client_factory'))
        self.assertIsNone(kwargs.get('bitbucket_client_factory'))

    def test_injectable_factories_forwarded_to_factory(self):
        cfg = OmegaConf.create({
            'base_url': 'https://api.github.com',
            'token': 'gh-token',
        })
        gh_factory = Mock()
        gl_factory = Mock()
        bb_factory = Mock()
        with patch(
            'repository_core_lib.repository_core_lib.repository_core_lib.PullRequestClientFactory',
        ) as mock_factory_class, patch(
            'repository_core_lib.repository_core_lib.repository_core_lib.PullRequestService',
        ):
            RepositoryCoreLib(
                cfg, 3,
                github_client_factory=gh_factory,
                gitlab_client_factory=gl_factory,
                bitbucket_client_factory=bb_factory,
            )

        _, kwargs = mock_factory_class.call_args
        self.assertIs(kwargs['github_client_factory'], gh_factory)
        self.assertIs(kwargs['gitlab_client_factory'], gl_factory)
        self.assertIs(kwargs['bitbucket_client_factory'], bb_factory)
