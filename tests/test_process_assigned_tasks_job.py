import types
import unittest
from unittest.mock import Mock

import bootstrap  # noqa: F401

from openhands_agent.jobs.process_assigned_tasks import ProcessAssignedTasksJob
from openhands_agent.openhands_agent_core_lib import OpenHandsAgentCoreLib
from utils import sync_create_start_core_lib


class ProcessAssignedTasksJobTests(unittest.TestCase):
    def setUp(self) -> None:
        self.job = ProcessAssignedTasksJob()
        self.openhands_core_lib = sync_create_start_core_lib()

    def test_initialized_accepts_openhands_agent_core_lib(self) -> None:
        self.job.initialized(self.openhands_core_lib)

        self.assertIs(self.job._data_handler, self.openhands_core_lib)
        self.assertIsInstance(self.job._data_handler, OpenHandsAgentCoreLib)

    def test_initialized_rejects_invalid_data_handler(self) -> None:
        with self.assertRaises(AssertionError):
            self.job.initialized(types.SimpleNamespace())

    def test_run_sends_failure_notification_before_reraising(self) -> None:
        self.openhands_core_lib.service = Mock()
        self.openhands_core_lib.service.process_assigned_tasks.side_effect = RuntimeError('service down')
        self.openhands_core_lib.notify_failure = Mock()
        self.job.initialized(self.openhands_core_lib)

        with self.assertRaisesRegex(RuntimeError, 'service down'):
            self.job.run()

        self.openhands_core_lib.notify_failure.assert_called_once()
