"""Full coverage for client/task_client_factory.py.

Split into three layers:

* ``InjectionTests`` — all 8 platforms via ``provider_factories`` dict;
  no platform libraries needed.
* ``DefaultPathTests`` — ``_build_default`` exercised through lazy-import
  patching (``sys.modules``); verifies each platform's config structure
  and ``max_retries`` propagation.
* ``NoneTests`` — edge cases that return ``None``.
"""
from __future__ import annotations

import sys
import unittest
from unittest.mock import MagicMock, patch

from omegaconf import OmegaConf

from task_core_lib.task_core_lib.client.task_client_factory import TaskClientFactory
from task_core_lib.task_core_lib.platform import Platform


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _minimal_cfg(**kwargs):
    base = {'base_url': 'https://example.com', 'token': 'tok'}
    base.update(kwargs)
    return OmegaConf.create(base)


def _mock_platform_lib(dotted_path: str):
    """Return a ``patch.dict`` context manager that plants a MagicMock at
    *dotted_path* (and all its parent segments) in ``sys.modules``."""
    parts = dotted_path.split('.')
    mocks = {}
    for i in range(len(parts)):
        mocks['.'.join(parts[: i + 1])] = MagicMock()
    leaf_mock = MagicMock()
    mocks[dotted_path] = leaf_mock
    return patch.dict(sys.modules, mocks), leaf_mock


# ---------------------------------------------------------------------------
# InjectionTests — provider_factories bypass
# ---------------------------------------------------------------------------


class InjectionTests(unittest.TestCase):
    """All 8 platforms, factory called with (config, max_retries)."""

    def _make_factory(self, platform: Platform, cfg=None, max_retries: int = 3):
        mock_issue = MagicMock()
        mock_fn = MagicMock(return_value=mock_issue)
        cfg = cfg or _minimal_cfg()
        client = TaskClientFactory(
            cfg, max_retries, provider_factories={platform: mock_fn},
        ).get(platform)
        return client, mock_issue, mock_fn, cfg

    def test_youtrack_injection(self) -> None:
        client, issue, fn, cfg = self._make_factory(Platform.YOUTRACK)
        self.assertIs(client, issue)
        fn.assert_called_once_with(cfg, 3)

    def test_jira_injection(self) -> None:
        client, issue, fn, cfg = self._make_factory(Platform.JIRA)
        self.assertIs(client, issue)
        fn.assert_called_once_with(cfg, 3)

    def test_github_injection(self) -> None:
        client, issue, fn, cfg = self._make_factory(Platform.GITHUB)
        self.assertIs(client, issue)
        fn.assert_called_once_with(cfg, 3)

    def test_github_issues_injection(self) -> None:
        client, issue, fn, cfg = self._make_factory(Platform.GITHUB_ISSUES)
        self.assertIs(client, issue)
        fn.assert_called_once_with(cfg, 3)

    def test_gitlab_injection(self) -> None:
        client, issue, fn, cfg = self._make_factory(Platform.GITLAB)
        self.assertIs(client, issue)
        fn.assert_called_once_with(cfg, 3)

    def test_gitlab_issues_injection(self) -> None:
        client, issue, fn, cfg = self._make_factory(Platform.GITLAB_ISSUES)
        self.assertIs(client, issue)
        fn.assert_called_once_with(cfg, 3)

    def test_bitbucket_injection(self) -> None:
        client, issue, fn, cfg = self._make_factory(Platform.BITBUCKET)
        self.assertIs(client, issue)
        fn.assert_called_once_with(cfg, 3)

    def test_bitbucket_issues_injection(self) -> None:
        client, issue, fn, cfg = self._make_factory(Platform.BITBUCKET_ISSUES)
        self.assertIs(client, issue)
        fn.assert_called_once_with(cfg, 3)

    def test_max_retries_passed_to_factory(self) -> None:
        captured = {}

        def fn(cfg, max_retries):
            captured['max_retries'] = max_retries
            return MagicMock()

        TaskClientFactory(
            _minimal_cfg(), 99,
            provider_factories={Platform.YOUTRACK: fn},
        ).get(Platform.YOUTRACK)
        self.assertEqual(captured['max_retries'], 99)

    def test_config_passed_to_factory(self) -> None:
        captured = {}

        def fn(cfg, max_retries):
            captured['cfg'] = cfg
            return MagicMock()

        cfg = _minimal_cfg(token='special-tok')
        TaskClientFactory(
            cfg, 1,
            provider_factories={Platform.JIRA: fn},
        ).get(Platform.JIRA)
        self.assertIs(captured['cfg'], cfg)

    def test_missing_platform_in_factories_returns_none(self) -> None:
        result = TaskClientFactory(
            _minimal_cfg(), 3,
            provider_factories={Platform.JIRA: MagicMock()},
        ).get(Platform.YOUTRACK)
        self.assertIsNone(result)

    def test_empty_factories_dict_returns_none(self) -> None:
        result = TaskClientFactory(
            _minimal_cfg(), 3, provider_factories={},
        ).get(Platform.YOUTRACK)
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# NoneTests — unsupported platform via default path
# ---------------------------------------------------------------------------


class NoneTests(unittest.TestCase):
    def test_unknown_platform_object_returns_none(self) -> None:
        factory = TaskClientFactory(_minimal_cfg(), 3)
        result = factory._build_default(MagicMock(spec=Platform))
        self.assertIsNone(result)

    def test_none_factories_with_unknown_platform_returns_none(self) -> None:
        # provider_factories=None triggers _build_default, which returns None
        # for an unrecognised platform.
        factory = TaskClientFactory(_minimal_cfg(), 3)
        result = factory._build_default(object())  # type: ignore[arg-type]
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# DefaultPathTests — lazy-import patching
# ---------------------------------------------------------------------------


class DefaultPathYouTrackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = OmegaConf.create({
            'base_url': 'https://yt.example.com',
            'token': 'yt-tok',
            'project': 'PROJ',
            'assignee': 'me',
        })

    def test_youtrack_client_built(self) -> None:
        ctx, leaf = _mock_platform_lib(
            'youtrack_core_lib.youtrack_core_lib.youtrack_core_lib'
        )
        with ctx:
            result = TaskClientFactory(self.cfg, 5).get(Platform.YOUTRACK)
        self.assertIs(result, leaf.YouTrackCoreLib.return_value.issue)
        leaf.YouTrackCoreLib.assert_called_once()

    def test_youtrack_config_base_url(self) -> None:
        ctx, leaf = _mock_platform_lib(
            'youtrack_core_lib.youtrack_core_lib.youtrack_core_lib'
        )
        with ctx:
            TaskClientFactory(self.cfg, 7).get(Platform.YOUTRACK)
        yt_cfg = leaf.YouTrackCoreLib.call_args.args[0].core_lib.youtrack_core_lib
        self.assertEqual(yt_cfg.base_url, self.cfg.base_url)

    def test_youtrack_config_token(self) -> None:
        ctx, leaf = _mock_platform_lib(
            'youtrack_core_lib.youtrack_core_lib.youtrack_core_lib'
        )
        with ctx:
            TaskClientFactory(self.cfg, 7).get(Platform.YOUTRACK)
        yt_cfg = leaf.YouTrackCoreLib.call_args.args[0].core_lib.youtrack_core_lib
        self.assertEqual(yt_cfg.token, self.cfg.token)

    def test_youtrack_config_max_retries(self) -> None:
        ctx, leaf = _mock_platform_lib(
            'youtrack_core_lib.youtrack_core_lib.youtrack_core_lib'
        )
        with ctx:
            TaskClientFactory(self.cfg, 7).get(Platform.YOUTRACK)
        yt_cfg = leaf.YouTrackCoreLib.call_args.args[0].core_lib.youtrack_core_lib
        self.assertEqual(yt_cfg.max_retries, 7)

    def test_youtrack_config_project_and_assignee(self) -> None:
        ctx, leaf = _mock_platform_lib(
            'youtrack_core_lib.youtrack_core_lib.youtrack_core_lib'
        )
        with ctx:
            TaskClientFactory(self.cfg, 1).get(Platform.YOUTRACK)
        yt_cfg = leaf.YouTrackCoreLib.call_args.args[0].core_lib.youtrack_core_lib
        self.assertEqual(yt_cfg.project, 'PROJ')
        self.assertEqual(yt_cfg.assignee, 'me')


class DefaultPathJiraTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = OmegaConf.create({
            'base_url': 'https://company.atlassian.net',
            'token': 'jira-tok',
            'email': 'user@example.com',
            'project': 'JIRA',
        })

    def test_jira_client_built(self) -> None:
        ctx, leaf = _mock_platform_lib(
            'jira_core_lib.jira_core_lib.jira_core_lib'
        )
        with ctx:
            result = TaskClientFactory(self.cfg, 4).get(Platform.JIRA)
        self.assertIs(result, leaf.JiraCoreLib.return_value.issue)

    def test_jira_config_structure(self) -> None:
        ctx, leaf = _mock_platform_lib(
            'jira_core_lib.jira_core_lib.jira_core_lib'
        )
        with ctx:
            TaskClientFactory(self.cfg, 4).get(Platform.JIRA)
        jira_cfg = leaf.JiraCoreLib.call_args.args[0].core_lib.jira_core_lib
        self.assertEqual(jira_cfg.base_url, self.cfg.base_url)
        self.assertEqual(jira_cfg.token, self.cfg.token)
        self.assertEqual(jira_cfg.email, self.cfg.email)
        self.assertEqual(jira_cfg.project, self.cfg.project)
        self.assertEqual(jira_cfg.max_retries, 4)


class DefaultPathBitbucketTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = OmegaConf.create({
            'base_url': 'https://api.bitbucket.org/2.0',
            'token': 'bb-tok',
            'username': 'bb-user',
            'api_email': 'user@example.com',
            'workspace': 'my-workspace',
            'repo_slug': 'my-repo',
        })

    def _run_platform(self, platform):
        ctx, leaf = _mock_platform_lib(
            'bitbucket_core_lib.bitbucket_core_lib.bitbucket_core_lib'
        )
        with ctx:
            result = TaskClientFactory(self.cfg, 6).get(platform)
        return result, leaf

    def test_bitbucket_client_built(self) -> None:
        result, leaf = self._run_platform(Platform.BITBUCKET)
        self.assertIs(result, leaf.BitbucketCoreLib.return_value.issue)

    def test_bitbucket_issues_alias_also_builds_client(self) -> None:
        result, leaf = self._run_platform(Platform.BITBUCKET_ISSUES)
        self.assertIs(result, leaf.BitbucketCoreLib.return_value.issue)

    def test_bitbucket_config_base_url(self) -> None:
        _, leaf = self._run_platform(Platform.BITBUCKET)
        bb_cfg = leaf.BitbucketCoreLib.call_args.args[0].core_lib.bitbucket_core_lib
        self.assertEqual(bb_cfg.base_url, self.cfg.base_url)

    def test_bitbucket_config_token(self) -> None:
        _, leaf = self._run_platform(Platform.BITBUCKET)
        bb_cfg = leaf.BitbucketCoreLib.call_args.args[0].core_lib.bitbucket_core_lib
        self.assertEqual(bb_cfg.token, self.cfg.token)

    def test_bitbucket_config_username(self) -> None:
        _, leaf = self._run_platform(Platform.BITBUCKET)
        bb_cfg = leaf.BitbucketCoreLib.call_args.args[0].core_lib.bitbucket_core_lib
        self.assertEqual(bb_cfg.username, 'bb-user')

    def test_bitbucket_config_api_email(self) -> None:
        _, leaf = self._run_platform(Platform.BITBUCKET)
        bb_cfg = leaf.BitbucketCoreLib.call_args.args[0].core_lib.bitbucket_core_lib
        self.assertEqual(bb_cfg.api_email, 'user@example.com')

    def test_bitbucket_config_workspace_and_repo_slug(self) -> None:
        _, leaf = self._run_platform(Platform.BITBUCKET)
        bb_cfg = leaf.BitbucketCoreLib.call_args.args[0].core_lib.bitbucket_core_lib
        self.assertEqual(bb_cfg.workspace, 'my-workspace')
        self.assertEqual(bb_cfg.repo_slug, 'my-repo')

    def test_bitbucket_config_max_retries(self) -> None:
        _, leaf = self._run_platform(Platform.BITBUCKET)
        bb_cfg = leaf.BitbucketCoreLib.call_args.args[0].core_lib.bitbucket_core_lib
        self.assertEqual(bb_cfg.max_retries, 6)

    def test_bitbucket_missing_optional_fields_default_to_empty(self) -> None:
        cfg = OmegaConf.create({
            'base_url': 'https://api.bitbucket.org/2.0',
            'token': 'tok',
        })
        ctx, leaf = _mock_platform_lib(
            'bitbucket_core_lib.bitbucket_core_lib.bitbucket_core_lib'
        )
        with ctx:
            TaskClientFactory(cfg, 1).get(Platform.BITBUCKET)
        bb_cfg = leaf.BitbucketCoreLib.call_args.args[0].core_lib.bitbucket_core_lib
        self.assertEqual(bb_cfg.username, '')
        self.assertEqual(bb_cfg.api_email, '')
        self.assertEqual(bb_cfg.workspace, '')
        self.assertEqual(bb_cfg.repo_slug, '')


class DefaultPathGitHubTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = OmegaConf.create({
            'base_url': 'https://api.github.com',
            'token': 'gh-tok',
            'owner': 'octo',
            'repo_slug': 'repo',
        })

    def _run_platform(self, platform):
        ctx, leaf = _mock_platform_lib(
            'github_core_lib.github_core_lib.github_core_lib'
        )
        with ctx:
            result = TaskClientFactory(self.cfg, 3).get(platform)
        return result, leaf

    def test_github_client_built(self) -> None:
        result, leaf = self._run_platform(Platform.GITHUB)
        self.assertIs(result, leaf.GitHubCoreLib.return_value.issue)

    def test_github_issues_alias_also_builds_client(self) -> None:
        result, leaf = self._run_platform(Platform.GITHUB_ISSUES)
        self.assertIs(result, leaf.GitHubCoreLib.return_value.issue)

    def test_github_config_structure(self) -> None:
        _, leaf = self._run_platform(Platform.GITHUB)
        gh_cfg = leaf.GitHubCoreLib.call_args.args[0].core_lib.github_core_lib
        self.assertEqual(gh_cfg.base_url, self.cfg.base_url)
        self.assertEqual(gh_cfg.token, self.cfg.token)
        self.assertEqual(gh_cfg.owner, self.cfg.owner)
        self.assertEqual(gh_cfg.repo_slug, self.cfg.repo_slug)
        self.assertEqual(gh_cfg.max_retries, 3)


class DefaultPathGitLabTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = OmegaConf.create({
            'base_url': 'https://gitlab.example/api/v4',
            'token': 'gl-tok',
            'project': 'group/repo',
        })

    def _run_platform(self, platform):
        ctx, leaf = _mock_platform_lib(
            'gitlab_core_lib.gitlab_core_lib.gitlab_core_lib'
        )
        with ctx:
            result = TaskClientFactory(self.cfg, 2).get(platform)
        return result, leaf

    def test_gitlab_client_built(self) -> None:
        result, leaf = self._run_platform(Platform.GITLAB)
        self.assertIs(result, leaf.GitLabCoreLib.return_value.issue)

    def test_gitlab_issues_alias_also_builds_client(self) -> None:
        result, leaf = self._run_platform(Platform.GITLAB_ISSUES)
        self.assertIs(result, leaf.GitLabCoreLib.return_value.issue)

    def test_gitlab_config_structure(self) -> None:
        _, leaf = self._run_platform(Platform.GITLAB)
        gl_cfg = leaf.GitLabCoreLib.call_args.args[0].core_lib.gitlab_core_lib
        self.assertEqual(gl_cfg.base_url, self.cfg.base_url)
        self.assertEqual(gl_cfg.token, self.cfg.token)
        self.assertEqual(gl_cfg.project, self.cfg.project)
        self.assertEqual(gl_cfg.max_retries, 2)


if __name__ == '__main__':
    unittest.main()
