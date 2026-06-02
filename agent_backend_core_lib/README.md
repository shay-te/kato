# agent-backend-core-lib

Small backend factory for autonomous coding-agent providers.

It owns the backend-selection layer that used to live in
`agent-core-lib`:

- `AgentBackendCoreLib`
- `AgentClientFactory`
- `AgentPlatform`
- `resolve_platform(...)`

## Responsibility

Given a configured platform plus application config, build the selected
backend client and expose it through the shared `AgentProvider`
contract.

```text
kato_core_lib
  -> agent_backend_core_lib
      -> claude_core_lib / codex_core_lib / openhands_core_lib
```

Backend imports are lazy. A Claude-only install does not import Codex or
OpenHands until that platform is selected.

## Non-responsibilities

- Prompt preparation and guardrail text live in `agent_core_lib`.
- Provider contracts and DTOs live in `agent_provider_contracts`.
- Backend transport lives in `claude_core_lib`, `codex_core_lib`, and
  `openhands_core_lib`.
- Raw LLM API calls belong in an LLM connection library, not here.

## Example

```python
from agent_backend_core_lib.agent_backend_core_lib import AgentBackendCoreLib
from agent_backend_core_lib.agent_backend_core_lib.client.agent_client_factory import (
    resolve_platform,
)

platform = resolve_platform(config.agent_backend)
agent = AgentBackendCoreLib(
    platform,
    config,
    max_retries=3,
    workspace_refusal_guidance='',
).agent

agent.implement_task(task, prepared_task=ctx)
```

## Tests

```bash
python -m unittest discover -s agent_backend_core_lib/agent_backend_core_lib/tests -p "test_*.py"
```
