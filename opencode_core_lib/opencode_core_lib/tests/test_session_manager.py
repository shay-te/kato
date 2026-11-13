"""``OpenCodeSessionManager``: the shared per-turn manager bound to OpenCode."""

from __future__ import annotations

import tempfile
import unittest
from unittest.mock import MagicMock

from agent_core_lib.agent_core_lib.data.agent_backend import AgentBackend
from agent_core_lib.agent_core_lib.session.record import AgentSessionRecord
from opencode_core_lib.opencode_core_lib.session.manager import OpenCodeSessionManager
from opencode_core_lib.opencode_core_lib.session.streaming import StreamingOpenCodeSession


class OpenCodeSessionManagerTests(unittest.TestCase):
    def test_it_is_bound_to_opencode(self) -> None:
        self.assertEqual(OpenCodeSessionManager.AGENT_BACKEND, AgentBackend.OPENCODE.value)
        self.assertIs(OpenCodeSessionManager.SESSION_CLASS, StreamingOpenCodeSession)

    def test_a_spawn_resumes_the_task_s_record_and_defaults_the_binary(self) -> None:
        built = []
        record = AgentSessionRecord(task_id='T-1', agent_backend='opencode', agent_session_id='ses_1')
        manager = OpenCodeSessionManager(
            state_dir=tempfile.mkdtemp(),
            session_factory=lambda **kwargs: built.append(kwargs) or MagicMock(is_alive=True),
            record_source=lambda task_id: record,
        )
        manager.start_session(task_id='T-1', cwd='/w')
        self.assertEqual(built[0]['binary'], 'opencode')
        self.assertEqual(built[0]['agent_session_id'], 'ses_1')


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
