from __future__ import annotations

from core_lib.core_lib import CoreLib
from omegaconf import DictConfig

from youtrack_core_lib.youtrack_core_lib.client.youtrack_client import YouTrackClient


class YouTrackCoreLib(CoreLib):
    """Compose the YouTrack issue client for Kato."""

    def __init__(self, cfg: DictConfig) -> None:
        super().__init__()
        youtrack_cfg = cfg.core_lib.youtrack_core_lib
        self.issue = YouTrackClient(
            youtrack_cfg.base_url,
            youtrack_cfg.token,
            youtrack_cfg.max_retries,
        )
