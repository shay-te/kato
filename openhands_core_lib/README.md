# openhands-core-lib

OpenHands agent backend. Implements
[`agent_provider_contracts.AgentProvider`](../agent_provider_contracts/)
so kato (and any other orchestrator) can call into OpenHands
through the same contract every other backend
([`claude_core_lib`](../claude_core_lib/), future
`codex_core_lib`) satisfies.

## What lives here

```
openhands_core_lib/openhands_core_lib/
└── openhands_client.py     ← KatoClient (implements AgentProvider)
```

## Public surface

```python
from openhands_core_lib.openhands_core_lib import KatoClient
```

`KatoClient` is what `agent_core_lib`'s factory returns when
`KATO_AGENT_BACKEND=openhands` (or unset, the historical default)
is configured.

## Runtime model

OpenHands is **HTTP RPC**, not streaming. `KatoClient` is a thin
HTTP client against an OpenHands service: `implement_task` →
HTTP → wait → result. The OpenHands service itself spawns a
docker "agent-server" container per conversation, but those
containers are an implementation detail of the OpenHands service,
not something kato sees or manages.

This is structurally different from Claude (which is an in-process
subprocess kato spawns directly). The difference is why
`AgentProvider` is operation-shaped, not transport-shaped — both
backends implement the same eight methods, but how they produce
the result is their own concern.

The streaming surface that `claude_core_lib` exposes
(`StreamingClaudeSession`) intentionally has no equivalent here.
The planning UI's chat tab is Claude-only.

## Configuration

Driven by Hydra under `core_lib.kato.openhands`:

```yaml
core_lib:
  kato:
    openhands:
      base_url: http://localhost:3000
      api_key: ${oc.env:OPENHANDS_API_KEY}
      poll_interval_seconds: 2.0
      max_poll_attempts: 900
      model_smoke_test_enabled: true
      llm_model: anthropic/claude-opus-4-7
      llm_api_key: ${oc.env:OPENHANDS_LLM_API_KEY}
```

When the configured LLM base URL points at OpenRouter the client
applies OpenRouter-specific request shaping; that adapter logic
lives inside `openhands_client.py`.

## Tests

```
openhands_core_lib/openhands_core_lib/tests/
```

49 tests as of writing — covers HTTP request shaping, conversation
lifecycle, OpenRouter adapter, retries.
