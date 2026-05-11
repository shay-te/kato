from __future__ import annotations

from core_lib.core_lib import CoreLib
from omegaconf import DictConfig

from jira_core_lib.jira_core_lib.client.jira_client import JiraClient


class JiraCoreLib(CoreLib):
    """Compose the Jira ticket client."""

    def __init__(self, cfg: DictConfig) -> None:
        super().__init__()
        jira_cfg = cfg.core_lib.jira_core_lib
        self.issue = JiraClient(
            jira_cfg.base_url,
            jira_cfg.token,
            jira_cfg.email,
            jira_cfg.max_retries,
        )
