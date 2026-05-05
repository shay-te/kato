# claude-core-lib

Claude Code CLI agent backend. Implements
[`agent_provider_contracts.AgentProvider`](../agent_provider_contracts/)
so kato (and any other orchestrator) can call into Claude through
the same contract every other backend
([`openhands_core_lib`](../openhands_core_lib/), future
`codex_core_lib`) satisfies.

## What lives here

```
claude_core_lib/claude_core_lib/
├── cli_client.py               ← ClaudeCliClient (implements AgentProvider)
├── streaming_session.py        ← long-lived stream-json subprocess (planning UI)
├── session_manager.py          ← per-task lifecycle for streaming sessions
├── session_history.py          ← reads ~/.claude/projects/ JSONL transcripts
├── claude_session_index.py     ← lists adoptable Claude Code sessions
└── wire_protocol.py            ← Claude CLI event-type constants
```

## Public surface

```python
from claude_core_lib.claude_core_lib import (
    ClaudeCliClient,         # AgentProvider impl, one-shot `claude -p`
    StreamingClaudeSession,  # long-lived stream-json session for the chat UI
    ClaudeSessionManager,    # per-task lifecycle for streaming sessions
)
```

`ClaudeCliClient` is what `agent_core_lib`'s factory returns when
`KATO_AGENT_BACKEND=claude` is configured. The streaming surface
(`StreamingClaudeSession` / `ClaudeSessionManager`) is intentionally
**not** part of `AgentProvider` — it has no OpenHands equivalent
and is consumed by kato's planning-UI server directly.

## What's owned here vs the sandbox

This package owns the Claude-specific runtime: subprocess wrapper,
NDJSON streaming, permission-prompt-tool stdio framing,
`--resume` session lifecycle, history replay.

The hardened-Docker boundary the spawn runs **inside** lives in
[`sandbox_core_lib`](../sandbox_core_lib/). `cli_client.py`
imports `sandbox_core_lib` for `wrap_command`, `enforce_no_workspace_secrets`,
`compose_system_prompt`, the credential scanner, and the workspace
delimiter framing — but never the other way around.

## Future: `cli_agent_runtime` extraction

The mechanical subprocess plumbing inside `streaming_session.py`
(spawn, NDJSON line reader, permission protocol stdio framing) is
generic enough that a future `codex_core_lib` will want it.
Extraction is deferred until that second consumer lands — designing
the shared utility from one impl is the trap; designing it from
two is what produces a good API. See
[`agent_provider_contracts/README.md`](../agent_provider_contracts/README.md)
for the broader rationale.

## Tests

```
claude_core_lib/claude_core_lib/tests/
```

Heaviest test set in the agent group (126 tests as of writing) —
the streaming session has a lot of state-machine corner cases,
all pinned.
