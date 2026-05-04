from __future__ import annotations

from core_lib.core_lib import CoreLib
from omegaconf import DictConfig

from task_core_lib.task_core_lib.client.task_client_factory import TaskClientFactory
from task_core_lib.task_core_lib.platform import Platform


class TaskCoreLib(CoreLib):
    """Compose the task issue provider for Kato."""

    def __init__(self, platform: Platform, cfg: DictConfig, max_retries: int) -> None:
        super().__init__()
        self.issue = TaskClientFactory(cfg, max_retries).get(platform)
