"""Live OpenCode chats for the host, one per task.

Everything a per-turn CLI's manager does is shared in
``agent_core_lib.session.per_turn_manager``. An OpenCode chat's identity is
the ``ses_…`` id on every event of its first turn; ``opencode run --session
<id>`` takes it as an argument.
"""

from __future__ import annotations

from agent_core_lib.agent_core_lib.data.agent_backend import AgentBackend
from agent_core_lib.agent_core_lib.session.per_turn_manager import PerTurnSessionManager
from opencode_core_lib.opencode_core_lib.session.streaming import StreamingOpenCodeSession


class OpenCodeSessionManager(PerTurnSessionManager):
    """Start, find, and tear down live OpenCode chats."""

    AGENT_BACKEND = AgentBackend.OPENCODE.value
    SESSION_CLASS = StreamingOpenCodeSession
    DISPLAY_NAME = 'OpenCode'
