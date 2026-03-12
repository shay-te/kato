import json
import logging

from core_lib.jobs.job import Job

from openhands_agent.openhands_agent_core_lib import OpenHandsAgentCoreLib


class ProcessAssignedTasksJob(Job):
    def __init__(self) -> None:
        self.logger = logging.getLogger(self.__class__.__name__)

    def initialized(self, data_handler: OpenHandsAgentCoreLib) -> None:
        assert isinstance(data_handler, OpenHandsAgentCoreLib)
        self._data_handler = data_handler

    def run(self) -> list[dict[str, str]]:
        try:
            results = self._data_handler.service.process_assigned_tasks()
            print(json.dumps(results))
            return results
        except Exception as exc:
            self.logger.exception('process_assigned_tasks_job failed')
            try:
                self._data_handler.service.notification_service.notify_failure(
                    'process_assigned_tasks_job',
                    exc,
                )
            except Exception:
                self.logger.exception(
                    'failed to send failure notification for process_assigned_tasks_job'
                )
            raise
