"""Coverage for ``GitHubCoreLib`` constructor."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from github_core_lib.github_core_lib.github_core_lib import GitHubCoreLib


class GitHubCoreLibInitTests(unittest.TestCase):
    def test_composes_pull_request_and_issue_clients(self) -> None:
        github_cfg = SimpleNamespace(
            base_url='https://api.github.com',
            token='ghp_test',
            max_retries=3,
            owner='owner',
        )
        github_cfg.get = lambda key, default='': {
            'repo': 'my-repo',
            'repo_slug': '',
        }.get(key, default)
        cfg = SimpleNamespace(core_lib=SimpleNamespace(
            github_core_lib=github_cfg,
        ))
        with patch(
            'github_core_lib.github_core_lib.github_core_lib.GitHubClient',
        ) as pr_client_cls, patch(
            'github_core_lib.github_core_lib.github_core_lib.GitHubIssuesClient',
        ) as issues_client_cls:
            lib = GitHubCoreLib(cfg)
        self.assertIsNotNone(lib.pull_request)
        self.assertIsNotNone(lib.issue)
        # Issue client got the repo from the ``repo`` key.
        issues_args = issues_client_cls.call_args.args
        self.assertIn('my-repo', issues_args)

    def test_falls_back_to_repo_slug_when_repo_blank(self) -> None:
        github_cfg = SimpleNamespace(
            base_url='https://api.github.com',
            token='t', max_retries=3, owner='owner',
        )
        github_cfg.get = lambda key, default='': {
            'repo': '',
            'repo_slug': 'fallback-slug',
        }.get(key, default)
        cfg = SimpleNamespace(core_lib=SimpleNamespace(
            github_core_lib=github_cfg,
        ))
        with patch(
            'github_core_lib.github_core_lib.github_core_lib.GitHubClient',
        ), patch(
            'github_core_lib.github_core_lib.github_core_lib.GitHubIssuesClient',
        ) as issues_client_cls:
            GitHubCoreLib(cfg)
        issues_args = issues_client_cls.call_args.args
        self.assertIn('fallback-slug', issues_args)


if __name__ == '__main__':
    unittest.main()
