"""Codex CLI backend for the orchestrator.

Mirrors every other CLI transport module for module (the layout is the
contract in ``agent_provider_contracts.cli_transport_layout``), so a reader who
has learned one backend recognises the others.

Public surface:
    CodexCliClient        - one-shot ``codex exec`` runs (implements AgentProvider).
    StreamingCodexSession - a chat: one ``codex exec`` process per turn.
    CodexSessionManager   - per-task lifecycle for those chats.
    SessionEvent          - one event in a chat's log.
"""

from codex_core_lib.codex_core_lib.cli_client import CodexCliClient
from codex_core_lib.codex_core_lib.session.manager import CodexSessionManager
from codex_core_lib.codex_core_lib.session.streaming import (
    SessionEvent,
    StreamingCodexSession,
)
