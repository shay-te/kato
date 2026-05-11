# openrouter-core-lib

Self-contained HTTP client for the [OpenRouter](https://openrouter.ai/)
LLM router. Used by `openhands_core_lib` (and historically by kato
itself) for connection + model-availability validation when the
configured LLM endpoint points at OpenRouter.

## What lives here

```
openrouter_core_lib/openrouter_core_lib/
└── openrouter_client.py      ← OpenRouterClient
```

## Public surface

```python
from openrouter_core_lib.openrouter_core_lib import OpenRouterClient

client = OpenRouterClient(base_url, token, max_retries=3)
client.validate_connection()
client.validate_model_available('anthropic/claude-opus-4-7')
```

`OpenRouterClient` extends `RetryingClientBase` (kato's HTTP base
+ retry convention) and adds OpenRouter-specific endpoints +
model-name normalisation.

## Why a separate package

Two consumers today:

- `openhands_core_lib` — when the configured LLM base URL points
  at OpenRouter, the OpenHands client validates the configured
  model exists via this client.
- `kato_core_lib` — boot-time `validate_env` and `configure_project`
  reach the same client.

Lives in its own sibling rather than under one consumer's
namespace because routing OpenRouter through one provider's
folder would force the other to import across `client/` boundaries
that don't exist. Same reasoning that put `vcs_provider_contracts`
in its own package — shared service clients deserve a neutral
home.

## Tests

```
openrouter_core_lib/openrouter_core_lib/tests/
```

Pin the request shape, the retry behaviour, and the
model-availability check.
