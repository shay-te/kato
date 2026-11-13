"""Live Grok chats for the host, one per task.

Everything a per-turn CLI's manager does is shared in
``agent_core_lib.session.per_turn_manager``. A Grok chat's identity is the
session id ``grok`` reports on the first line of its first turn (the
``system``/``init`` event); ``grok --resume <id>`` takes it as an argument.
"""

from __future__ import annotations

from agent_core_lib.agent_core_lib.data.agent_backend import AgentBackend
from agent_core_lib.agent_core_lib.session.per_turn_manager import PerTurnSessionManager
from grok_core_lib.grok_core_lib.session.streaming import StreamingGrokSession


class GrokSessionManager(PerTurnSessionManager):
    """Start, find, and tear down live Grok chats."""

    AGENT_BACKEND = AgentBackend.GROK.value
    SESSION_CLASS = StreamingGrokSession
    DISPLAY_NAME = 'Grok'
