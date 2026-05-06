from __future__ import annotations

from core_lib.core_lib import CoreLib
from omegaconf import DictConfig

from gitlab_core_lib.gitlab_core_lib.client.gitlab_client import GitLabClient
from gitlab_core_lib.gitlab_core_lib.client.gitlab_issues_client import GitLabIssuesClient


class GitLabCoreLib(CoreLib):
    """Compose GitLab repository and issue clients."""

    def __init__(self, cfg: DictConfig) -> None:
        super().__init__()
        gitlab_cfg = cfg.core_lib.gitlab_core_lib
        self.pull_request = GitLabClient(
            gitlab_cfg.base_url,
            gitlab_cfg.token,
            gitlab_cfg.max_retries,
        )
        self.issue = GitLabIssuesClient(
            gitlab_cfg.base_url,
            gitlab_cfg.token,
            gitlab_cfg.project,
            gitlab_cfg.max_retries,
        )
