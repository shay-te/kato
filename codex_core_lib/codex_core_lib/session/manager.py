"""Live Codex chats for the host, one per task.

Everything a per-turn CLI's manager does — the live per-task sessions, the
record written at spawn and again once the first turn reports its id, the
cross-backend guards — is shared in
``agent_core_lib.session.per_turn_manager``. A Codex chat's identity is the
thread id ``codex exec`` reports on its first turn; ``codex exec resume``
takes it as an argument and needs no file in any particular place, so none of
the persistent-process manager's transcript-relocation machinery applies.
"""

from __future__ import annotations

from agent_core_lib.agent_core_lib.data.agent_backend import AgentBackend
from agent_core_lib.agent_core_lib.session.per_turn_manager import PerTurnSessionManager
from codex_core_lib.codex_core_lib.session.streaming import StreamingCodexSession


class CodexSessionManager(PerTurnSessionManager):
    """Start, find, and tear down live Codex chats."""

    AGENT_BACKEND = AgentBackend.CODEX.value
    SESSION_CLASS = StreamingCodexSession
    DISPLAY_NAME = 'Codex'
