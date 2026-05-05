"""OpenHands agent backend.

Implements ``agent_provider_contracts.AgentProvider`` so kato (and
any other orchestrator) can call into OpenHands through the same
contract every other backend (claude_core_lib, future
codex_core_lib) satisfies.

Owns the OpenHands-specific runtime: HTTP client against the
OpenHands service, conversation lifecycle, OpenRouter LLM
configuration sync. Notably does NOT have a streaming session —
OpenHands is RPC-shaped (send a prompt, wait for the result), so
the planning UI's chat-tab streaming surface is Claude-only and
lives in ``claude_core_lib``.

Public surface:
    KatoClient - HTTP client driving an OpenHands server
                 (implements AgentProvider).
"""

from openhands_core_lib.openhands_core_lib.openhands_client import KatoClient  # noqa: F401
