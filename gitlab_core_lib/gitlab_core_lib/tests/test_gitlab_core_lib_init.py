"""Coverage for ``GitLabCoreLib`` constructor."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from gitlab_core_lib.gitlab_core_lib.gitlab_core_lib import GitLabCoreLib


class GitLabCoreLibInitTests(unittest.TestCase):
    def test_composes_pull_request_and_issue_clients(self) -> None:
        gitlab_cfg = SimpleNamespace(
            base_url='https://gitlab.example.com',
            token='glpat-test',
            max_retries=3,
            project='group/project',
        )
        cfg = SimpleNamespace(core_lib=SimpleNamespace(
            gitlab_core_lib=gitlab_cfg,
        ))
        with patch(
            'gitlab_core_lib.gitlab_core_lib.gitlab_core_lib.GitLabClient',
        ), patch(
            'gitlab_core_lib.gitlab_core_lib.gitlab_core_lib.GitLabIssuesClient',
        ):
            lib = GitLabCoreLib(cfg)
        self.assertIsNotNone(lib.pull_request)
        self.assertIsNotNone(lib.issue)


class ConfigPackageImportTests(unittest.TestCase):
    def test_config_package_imports_cleanly(self) -> None:
        from gitlab_core_lib.gitlab_core_lib import config  # noqa: F401


if __name__ == '__main__':
    unittest.main()
