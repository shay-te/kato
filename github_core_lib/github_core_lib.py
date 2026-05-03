from __future__ import annotations

from core_lib.core_lib import CoreLib
from omegaconf import DictConfig

from github_core_lib.client.github_client import GitHubClient
from github_core_lib.client.github_issues_client import GitHubIssuesClient


class GitHubCoreLib(CoreLib):
    """Compose GitHub repository and issue clients for Kato."""

    def __init__(self, cfg: DictConfig) -> None:
        super().__init__()
        github_cfg = cfg['core-lib']['github-core-lib']
        repo = github_cfg.get('repo', '') or github_cfg.get('repo_slug', '')
        self.pull_request = GitHubClient(
            github_cfg.base_url,
            github_cfg.token,
            github_cfg.max_retries,
        )
        self.issue = GitHubIssuesClient(
            github_cfg.base_url,
            github_cfg.token,
            github_cfg.owner,
            repo,
            github_cfg.max_retries,
        )
