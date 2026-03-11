from __future__ import annotations

import hydra
from omegaconf import DictConfig

from openhands_agent.openhands_agent_instance import OpenHandsAgentInstance


@hydra.main(version_base=None, config_path=".", config_name="core_lib_config.yaml")
def main(cfg: DictConfig) -> int:
    OpenHandsAgentInstance.init(cfg)
    app = OpenHandsAgentInstance.get()
    app.logger.info("starting openhands agent")
    results = app.process_assigned_tasks()
    app.logger.info("processed %s tasks", len(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
