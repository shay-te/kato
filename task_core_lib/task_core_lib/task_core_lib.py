from __future__ import annotations

from core_lib.core_lib import CoreLib
from omegaconf import DictConfig

from task_core_lib.task_core_lib.client.task_client_factory import TaskClientFactory


class TaskCoreLib(CoreLib):
    """Compose the task issue provider for Kato."""

    def __init__(self, issue_platform: str, cfg: DictConfig, max_retries: int) -> None:
        super().__init__()
        self.issue = TaskClientFactory(cfg, max_retries).get(issue_platform)
