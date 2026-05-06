"""OpenRouter API client.

Self-contained service client for the OpenRouter LLM router.
Used by ``openhands_core_lib`` (and historically by kato itself
for model-access validation when an OpenRouter base URL was
configured). Lives in its own sibling package so any backend that
wants to validate OpenRouter-hosted models has one canonical
import location.

Public surface:
    OpenRouterClient - HTTP client. Validates connection +
                       model availability against the configured
                       OpenRouter endpoint.
"""

from openrouter_core_lib.openrouter_core_lib.openrouter_client import (  # noqa: F401
    OpenRouterClient,
)
