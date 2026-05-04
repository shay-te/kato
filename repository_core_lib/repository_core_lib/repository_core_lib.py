from __future__ import annotations

from core_lib.core_lib import CoreLib
from omegaconf import DictConfig

from repository_core_lib.repository_core_lib.client.pull_request_client_factory import (
    PullRequestClientFactory,
)
from repository_core_lib.repository_core_lib.pull_request_service import PullRequestService


class RepositoryCoreLib(CoreLib):
    """Compose the repository pull-request service for a provider."""

    def __init__(self, cfg: DictConfig, max_retries: int) -> None:
        super().__init__()
        pull_request_client_factory = PullRequestClientFactory(cfg, max_retries)
        self.pull_request = PullRequestService(pull_request_client_factory)
