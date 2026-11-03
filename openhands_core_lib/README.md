# openhands-core-lib

OpenHands agent backend. Implements
[`agent_provider_contracts.AgentProvider`](../agent_provider_contracts/)
so any orchestrator can call into OpenHands through the same contract
the other backends ([`claude_core_lib`](../claude_core_lib/),
[`codex_core_lib`](../codex_core_lib/)) satisfy.

## What lives here

```
openhands_core_lib/openhands_core_lib/
├── openhands_client.py     ← OpenHandsClient (implements AgentProvider)
├── config_utils.py         ← base URL + LLM settings from a config block
├── data/, helpers/
└── tests/
```

## Public surface

```python
from openhands_core_lib.openhands_core_lib import OpenHandsClient
```

`OpenHandsClient` is what
[`agent_backend_core_lib`](../agent_backend_core_lib/)'s factory returns
when the host picks the `openhands` backend.

## Runtime model

OpenHands is **HTTP RPC**, not streaming. `OpenHandsClient` is a thin
HTTP client against an OpenHands service: `implement_task` →
HTTP → wait → result. The OpenHands service itself spawns a
docker "agent-server" container per conversation, but those
containers are an implementation detail of the OpenHands service,
not something the host sees or manages.

This is structurally different from Claude (a subprocess the host
spawns directly). The difference is why `AgentProvider` is
operation-shaped, not transport-shaped: every backend implements the
same methods, and how it produces the result is its own concern.

The streaming surface that `claude_core_lib` exposes
(`StreamingClaudeSession`) intentionally has no equivalent here.

## Configuration

The host passes an `openhands` config block; the factory reads:

| Field | Default |
|---|---|
| `base_url`, `api_key` | required |
| `llm_model`, `llm_base_url` | empty |
| `poll_interval_seconds` | `2.0` |
| `max_poll_attempts` | `900` |
| `model_smoke_test_enabled` | `true` |
| `testing_container_enabled`, `testing_base_url`, `testing_llm_model`, `testing_llm_base_url` | used for the testing run only |

Product text reaches the client as constructor parameters
(`workspace_refusal_guidance`, `self_reply_prefixes`), never from this
lib. When the configured LLM base URL points at OpenRouter the client
applies OpenRouter-specific request shaping; that logic lives inside
`openhands_client.py`.

## Tests

```
openhands_core_lib/openhands_core_lib/tests/
```

Covers HTTP request shaping, conversation lifecycle, the OpenRouter
adapter and retries.
