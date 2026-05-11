from __future__ import annotations

import unittest
from unittest.mock import Mock

from omegaconf import OmegaConf

from repository_core_lib.repository_core_lib.client.pull_request_client_factory import (
    PullRequestClientFactory,
)
from repository_core_lib.repository_core_lib.platform import Platform


def _github_cfg():
    return OmegaConf.create({
        'base_url': 'https://api.github.com',
        'token': 'gh-token',
        'owner': 'octo',
        'repo_slug': 'repo',
    })


def _gitlab_cfg():
    return OmegaConf.create({
        'base_url': 'https://gitlab.example/api/v4',
        'token': 'gl-token',
        'project': 'group/repo',
    })


def _bitbucket_cfg(**extra):
    return OmegaConf.create({
        'base_url': 'https://api.bitbucket.org/2.0',
        'token': 'bb-token',
        'username': 'bb-user',
        'workspace': 'workspace',
        'repo_slug': 'repo',
        **extra,
    })


# ---------------------------------------------------------------------------
# Injection: injectable factory callables
# ---------------------------------------------------------------------------


class PullRequestClientFactoryInjectableTests(unittest.TestCase):
    def test_injectable_github_factory_is_called(self):
        client = Mock()
        github_factory = Mock(return_value=client)
        factory = PullRequestClientFactory(_github_cfg(), 3, github_client_factory=github_factory)

        result = factory.get(Platform.GITHUB)

        self.assertIs(result, client)
        github_factory.assert_called_once()

    def test_injectable_gitlab_factory_is_called(self):
        client = Mock()
        gitlab_factory = Mock(return_value=client)
        factory = PullRequestClientFactory(_gitlab_cfg(), 3, gitlab_client_factory=gitlab_factory)

        result = factory.get(Platform.GITLAB)

        self.assertIs(result, client)
        gitlab_factory.assert_called_once()

    def test_injectable_bitbucket_factory_is_called(self):
        client = Mock()
        bitbucket_factory = Mock(return_value=client)
        factory = PullRequestClientFactory(_bitbucket_cfg(), 3, bitbucket_client_factory=bitbucket_factory)

        result = factory.get(Platform.BITBUCKET)

        self.assertIs(result, client)
        bitbucket_factory.assert_called_once()

    def test_github_injectable_factory_receives_config_with_max_retries(self):
        received = {}

        def capture_factory(config):
            received['config'] = config
            return Mock()

        factory = PullRequestClientFactory(_github_cfg(), 7, github_client_factory=capture_factory)
        factory.get(Platform.GITHUB)

        cfg = received['config']
        self.assertEqual(cfg.core_lib.github_core_lib.max_retries, 7)

    def test_gitlab_injectable_factory_receives_config_with_max_retries(self):
        received = {}

        def capture_factory(config):
            received['config'] = config
            return Mock()

        factory = PullRequestClientFactory(_gitlab_cfg(), 5, gitlab_client_factory=capture_factory)
        factory.get(Platform.GITLAB)

        cfg = received['config']
        self.assertEqual(cfg.core_lib.gitlab_core_lib.max_retries, 5)

    def test_bitbucket_injectable_factory_receives_correct_config(self):
        received = {}

        def capture_factory(config):
            received['config'] = config
            return Mock()

        cfg = _bitbucket_cfg(username='alice', workspace='acme', api_email='alice@acme.com')
        factory = PullRequestClientFactory(cfg, 2, bitbucket_client_factory=capture_factory)
        factory.get(Platform.BITBUCKET)

        bb = received['config'].core_lib.bitbucket_core_lib
        self.assertEqual(bb.base_url, 'https://api.bitbucket.org/2.0')
        self.assertEqual(bb.token, 'bb-token')
        self.assertEqual(bb.username, 'alice')
        self.assertEqual(bb.workspace, 'acme')
        self.assertEqual(bb.api_email, 'alice@acme.com')
        self.assertEqual(bb.max_retries, 2)

    def test_bitbucket_optional_fields_default_to_empty_string(self):
        received = {}

        def capture_factory(config):
            received['config'] = config
            return Mock()

        minimal_cfg = OmegaConf.create({'base_url': 'https://api.bitbucket.org/2.0', 'token': 'tok'})
        factory = PullRequestClientFactory(minimal_cfg, 1, bitbucket_client_factory=capture_factory)
        factory.get(Platform.BITBUCKET)

        bb = received['config'].core_lib.bitbucket_core_lib
        self.assertEqual(bb.username, '')
        self.assertEqual(bb.api_email, '')
        self.assertEqual(bb.workspace, '')
        self.assertEqual(bb.repo_slug, '')


# ---------------------------------------------------------------------------
# Unsupported platform
# ---------------------------------------------------------------------------


class PullRequestClientFactoryUnsupportedTests(unittest.TestCase):
    def test_rejects_unsupported_platform_with_exception(self):
        factory = PullRequestClientFactory(_github_cfg(), 3)
        with self.assertRaises(Exception) as ctx:
            factory.get(Mock())
        self.assertIn('unsupported repository provider', str(ctx.exception))

    def test_rejects_unsupported_platform_has_404_status_code(self):
        factory = PullRequestClientFactory(_github_cfg(), 3)
        with self.assertRaises(Exception) as ctx:
            factory.get(Mock())
        exc = ctx.exception
        if hasattr(exc, 'status_code'):
            self.assertEqual(exc.status_code, 404)

    def test_raw_get_returns_none_for_unsupported_platform(self):
        factory = PullRequestClientFactory(_github_cfg(), 3)
        result = PullRequestClientFactory.get.__wrapped__(factory, Mock())
        self.assertIsNone(result)

    def test_raw_get_returns_none_for_string_platform(self):
        factory = PullRequestClientFactory(_github_cfg(), 3)
        result = PullRequestClientFactory.get.__wrapped__(factory, 'unknown')
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# Max retries
# ---------------------------------------------------------------------------


class PullRequestClientFactoryMaxRetriesTests(unittest.TestCase):
    def test_github_config_includes_max_retries(self):
        received = {}

        def capture(config):
            received['config'] = config
            return Mock()

        PullRequestClientFactory(_github_cfg(), 10, github_client_factory=capture).get(Platform.GITHUB)
        self.assertEqual(received['config'].core_lib.github_core_lib.max_retries, 10)

    def test_gitlab_config_includes_max_retries(self):
        received = {}

        def capture(config):
            received['config'] = config
            return Mock()

        PullRequestClientFactory(_gitlab_cfg(), 8, gitlab_client_factory=capture).get(Platform.GITLAB)
        self.assertEqual(received['config'].core_lib.gitlab_core_lib.max_retries, 8)

    def test_bitbucket_config_includes_max_retries(self):
        received = {}

        def capture(config):
            received['config'] = config
            return Mock()

        PullRequestClientFactory(_bitbucket_cfg(), 6, bitbucket_client_factory=capture).get(Platform.BITBUCKET)
        self.assertEqual(received['config'].core_lib.bitbucket_core_lib.max_retries, 6)

    def test_max_retries_one_is_accepted(self):
        client = Mock()
        factory = PullRequestClientFactory(_github_cfg(), 1, github_client_factory=lambda _: client)
        result = factory.get(Platform.GITHUB)
        self.assertIs(result, client)
