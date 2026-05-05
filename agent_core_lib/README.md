# agent-core-lib

Factory wrapper that picks the configured agent backend (Claude /
OpenHands / future Codex) and exposes it through the shared
[`AgentProvider`](../agent_provider_contracts/) interface. Same
shape as [`task_core_lib`](../task_core_lib/) and
[`repository_core_lib`](../repository_core_lib/).

## What lives here

```
agent_core_lib/agent_core_lib/
├── agent_core_lib.py                ← AgentCoreLib composition root
├── platform.py                       ← AgentPlatform enum
└── client/
    └── agent_client_factory.py       ← AgentClientFactory + resolve_platform()
```

## Public surface

```python
from agent_core_lib.agent_core_lib import AgentCoreLib, AgentPlatform
from agent_core_lib.agent_core_lib.client.agent_client_factory import resolve_platform

platform = resolve_platform(cfg.kato.agent_backend)  # 'claude' / 'openhands' / aliases
agent = AgentCoreLib(
    platform,
    cfg.kato,
    max_retries=3,
    docker_mode_on=True,
    read_only_tools_on=False,
).agent
# agent is typed as AgentProvider — call by interface, never branch on backend.
agent.implement_task(task, prepared_task=ctx)
```

## What this lib owns vs what it doesn't

- **Owns**: backend selection (`KATO_AGENT_BACKEND` → `AgentPlatform`),
  alias resolution (`claude-code` / `claude_cli` / etc.), per-backend
  construction (passes runtime knobs through to each impl).
- **Does NOT own**: the actual agent runtime. Each impl
  ([`claude_core_lib`](../claude_core_lib/),
  [`openhands_core_lib`](../openhands_core_lib/)) brings its own
  subprocess / HTTP plumbing. This factory is a thin wrapper —
  same shape as `TaskClientFactory` for ticket platforms.

## Why a factory pattern at all

Kato used to have `if is_claude_backend(): build_claude_client(...) else: build_openhands_client(...)` branches scattered through its composition root. Each new backend added one more branch and one more `KatoClient | ClaudeCliClient` union type. The factory collapses that to one place; kato sees only `AgentProvider` past the boot wire-up.

Adding a new backend (e.g., future `codex_core_lib`) means:
1. Add `CODEX = 'codex'` to `AgentPlatform`.
2. Wire its alias(es) in `_PLATFORM_ALIASES`.
3. Add a `_build_codex` branch in `AgentClientFactory.build`.
Kato itself doesn't change.

## Tests

```
agent_core_lib/agent_core_lib/tests/
```

Pin: alias resolution (every operator-typed string maps to the
right enum), unknown-backend error message (must name the supported
options so the operator knows what to fix), factory dispatch
(routes the right enum to the right builder).

Construction of the actual backend objects is tested where they
live — `claude_core_lib` tests cover `ClaudeCliClient` construction,
`openhands_core_lib` tests cover `KatoClient` construction.
