"""OpenHands agent backend.

Implements ``agent_provider_contracts.AgentProvider`` so any
orchestrator can call into OpenHands through the same contract
every other backend satisfies.

Owns the OpenHands-specific runtime: HTTP client against the
OpenHands service, conversation lifecycle, OpenRouter LLM
configuration sync. Notably does NOT have a streaming session —
OpenHands is RPC-shaped (send a prompt, wait for the result).

Public surface:
    OpenHandsClient - HTTP client driving an OpenHands server
                      (implements AgentProvider).
"""

from openhands_core_lib.openhands_core_lib.openhands_client import OpenHandsClient  # noqa: F401

KatoClient = OpenHandsClient  # backward-compatibility alias
