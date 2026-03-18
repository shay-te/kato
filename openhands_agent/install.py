import hydra
from omegaconf import DictConfig

from openhands_agent.openhands_agent_core_lib import OpenHandsAgentCoreLib


@hydra.main(
    version_base=None,
    config_path='config',
    config_name='openhands_agent_core_lib',
)
def main(cfg: DictConfig) -> int:
    OpenHandsAgentCoreLib.install(cfg)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
