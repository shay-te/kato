from __future__ import annotations

from typing import TYPE_CHECKING

from core_lib.data_layers.service.service import Service

from kato_core_lib.helpers.retry_utils import retry_count
from kato_core_lib.data_layers.data.task import Task
from kato_core_lib.helpers.task_context_utils import PreparedTaskContext
from kato_core_lib.helpers.logging_utils import configure_logger

if TYPE_CHECKING:
    from kato_core_lib.client.agent_client import AgentClient


class TestingService(Service):
    """Delegate testing validation for a task to the active agent client."""
    def __init__(self, client: 'AgentClient') -> None:
        self._client = client
        self.logger = configure_logger(self.__class__.__name__)

    @property
    def max_retries(self) -> int:
        return retry_count(getattr(self._client, 'max_retries', 1))

    def validate_connection(self) -> None:
        self._client.validate_connection()

    def validate_model_access(self) -> None:
        self._client.validate_model_access()

    def stop_all_conversations(self) -> None:
        self._client.stop_all_conversations()

    def test_task(
        self,
        task: Task,
        prepared_task: PreparedTaskContext | None = None,
    ) -> dict[str, str | bool]:
        self.logger.info('delegating testing validation for task %s', task.id)
        return self._client.test_task(task, prepared_task=prepared_task)
