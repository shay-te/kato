import hydra
from omegaconf import DictConfig

from openhands_agent.openhands_agent_instance import OpenHandsAgentInstance


@hydra.main(version_base=None, config_path='config', config_name='core_lib')
def main(cfg: DictConfig) -> int:
    OpenHandsAgentInstance.init(cfg)
    app = OpenHandsAgentInstance.get()
    app.logger.info('starting openhands agent')
    try:
        results = app.service.process_assigned_tasks()
    except Exception as exc:
        app.notify_failure('process_assigned_tasks', exc)
        raise
    app.logger.info('processed %s tasks', len(results))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
