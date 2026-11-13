"""OpenCode CLI backend (``opencode``) for the orchestrator.

Mirrors every other CLI transport module for module (the layout is the
contract in ``agent_provider_contracts.cli_transport_layout``), so a reader who
has learned one backend recognises the others.

Public surface:
    OpenCodeCliClient        - one-shot ``opencode run`` runs (implements AgentProvider).
    StreamingOpenCodeSession - a chat: one ``opencode run`` process per turn.
    OpenCodeSessionManager   - per-task lifecycle for those chats.
    SessionEvent             - one event in a chat's log.
"""

from opencode_core_lib.opencode_core_lib.cli_client import OpenCodeCliClient
from opencode_core_lib.opencode_core_lib.session.manager import OpenCodeSessionManager
from opencode_core_lib.opencode_core_lib.session.streaming import (
    SessionEvent,
    StreamingOpenCodeSession,
)
