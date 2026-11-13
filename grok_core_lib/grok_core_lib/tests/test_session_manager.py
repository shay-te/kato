"""``GrokSessionManager``: the shared per-turn manager bound to Grok."""

from __future__ import annotations

import tempfile
import unittest
from unittest.mock import MagicMock

from agent_core_lib.agent_core_lib.data.agent_backend import AgentBackend
from agent_core_lib.agent_core_lib.session.record import AgentSessionRecord
from grok_core_lib.grok_core_lib.session.manager import GrokSessionManager
from grok_core_lib.grok_core_lib.session.streaming import StreamingGrokSession


class GrokSessionManagerTests(unittest.TestCase):
    def test_it_is_bound_to_grok(self) -> None:
        self.assertEqual(GrokSessionManager.AGENT_BACKEND, AgentBackend.GROK.value)
        self.assertIs(GrokSessionManager.SESSION_CLASS, StreamingGrokSession)

    def test_a_spawn_resumes_the_task_s_grok_record_and_defaults_the_binary(self) -> None:
        built = []

        def factory(**kwargs):
            built.append(kwargs)
            return MagicMock(is_alive=True)

        record = AgentSessionRecord(task_id='T-1', agent_backend='grok', agent_session_id='sess-1')
        sink = MagicMock()
        manager = GrokSessionManager(
            state_dir=tempfile.mkdtemp(), session_factory=factory,
            record_source=lambda task_id: record, record_sink=sink,
        )
        manager.start_session(task_id='T-1', cwd='/w')
        self.assertEqual(built[0]['binary'], 'grok')
        self.assertEqual(built[0]['agent_session_id'], 'sess-1')
        sink.assert_called_once()


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
