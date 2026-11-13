"""Grok Build CLI backend (xAI's ``grok``) for the orchestrator.

Mirrors every other CLI transport module for module (the layout is the
contract in ``agent_provider_contracts.cli_transport_layout``), so a reader who
has learned one backend recognises the others.

Public surface:
    GrokCliClient        - one-shot ``grok -p`` runs (implements AgentProvider).
    StreamingGrokSession - a chat: one ``grok -p`` process per turn.
    GrokSessionManager   - per-task lifecycle for those chats.
    SessionEvent         - one event in a chat's log.
"""

from grok_core_lib.grok_core_lib.cli_client import GrokCliClient
from grok_core_lib.grok_core_lib.session.manager import GrokSessionManager
from grok_core_lib.grok_core_lib.session.streaming import (
    SessionEvent,
    StreamingGrokSession,
)
