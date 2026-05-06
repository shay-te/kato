"""OpenRouter API client.

Self-contained service client for the OpenRouter LLM router.
Any backend that wants to validate OpenRouter-hosted models
has one canonical import location.

Public surface:
    OpenRouterClient - HTTP client. Validates connection +
                       model availability against the configured
                       OpenRouter endpoint.
"""

from openrouter_core_lib.openrouter_core_lib.openrouter_client import (  # noqa: F401
    OpenRouterClient,
)
