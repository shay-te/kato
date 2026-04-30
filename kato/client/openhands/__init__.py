"""OpenHands backend for Kato.

Public surface:
    KatoClient        - HTTP client that drives an OpenHands server.
    OpenRouterClient  - thin auxiliary used by KatoClient to validate
                        OpenRouter-hosted models before spending a slot.

KatoClient implements the :class:`kato.client.agent_client.AgentClient`
contract.
"""

from kato.client.openhands.openhands_client import KatoClient
from kato.client.openhands.openrouter_client import OpenRouterClient
