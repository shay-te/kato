from __future__ import annotations

from omegaconf import DictConfig

from core_lib.core_lib import CoreLib

from openhands_agent.services.agent_service import AgentService


class OpenHandsAgentCoreLib(CoreLib):
    def __init__(self, cfg: DictConfig) -> None:
        CoreLib.__init__(self)
        self.config = cfg
        self.agent_service = AgentService(cfg)

    def process_assigned_tasks(self) -> list[dict[str, str]]:
        return self.agent_service.process_assigned_tasks()

    def handle_pull_request_comment(self, payload: dict) -> dict[str, str]:
        return self.agent_service.handle_pull_request_comment(payload)
