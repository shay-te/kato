from __future__ import annotations

from core_lib.core_lib import CoreLib
from omegaconf import DictConfig

from azure_devops_core_lib.azure_devops_core_lib.client.azure_devops_client import (
    AzureDevOpsClient,
)


class AzureDevOpsCoreLib(CoreLib):
    """Compose the Azure Repos pull-request client.

    Pull requests only: Azure Boards (work items as tasks) is not provided.
    """

    def __init__(self, cfg: DictConfig) -> None:
        super().__init__()
        azure_cfg = cfg.core_lib.azure_devops_core_lib
        self.pull_request = AzureDevOpsClient(
            azure_cfg.base_url,
            azure_cfg.token,
            azure_cfg.max_retries,
        )
