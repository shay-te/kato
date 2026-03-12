import unittest
from unittest.mock import patch

import bootstrap  # noqa: F401

from openhands_agent.openhands_agent_instance import OpenHandsAgentInstance
from utils import build_test_cfg


class OpenHandsAgentInstanceTests(unittest.TestCase):
    def tearDown(self) -> None:
        OpenHandsAgentInstance._app_instance = None

    def test_get_raises_before_init(self) -> None:
        OpenHandsAgentInstance._app_instance = None

        with self.assertRaisesRegex(RuntimeError, 'OpenHandsAgentCoreLib is not initialized'):
            OpenHandsAgentInstance.get()

    def test_init_is_idempotent(self) -> None:
        cfg = build_test_cfg()
        with patch(
            'openhands_agent.openhands_agent_core_lib.AgentService.validate_connections'
        ):
            OpenHandsAgentInstance.init(cfg)
            first = OpenHandsAgentInstance.get()
            OpenHandsAgentInstance.init(cfg)
            second = OpenHandsAgentInstance.get()

        self.assertIs(first, second)
