# agent-provider-contracts

Pure ABCs + DTOs every agent-backend implementation satisfies.
Mirror of `vcs_provider_contracts` but for the agent side
(`AgentProvider`) instead of the VCS side (`IssueProvider` /
`PullRequestProvider`).

## What lives here

```
agent_provider_contracts/agent_provider_contracts/
├── agent_provider.py           ← Protocol every backend implements
├── agent_task.py               ← DTO: AgentTask
├── agent_review_comment.py     ← DTO: AgentReviewComment
├── prepared_task_context.py    ← DTO: AgentPreparedTaskContext
└── agent_result.py             ← type alias for the result dict
```

## The contract

Eight methods every backend implements. Two health checks, four
real-work entry points, two lifecycle ops:

```python
class AgentProvider(Protocol):
    def validate_connection(self) -> None: ...
    def validate_model_access(self) -> None: ...
    def implement_task(self, task, session_id='', prepared_task=None) -> AgentResult: ...
    def test_task(self, task, prepared_task=None) -> AgentResult: ...
    def fix_review_comment(self, comment, branch_name, ...) -> AgentResult: ...
    def fix_review_comments(self, comments, branch_name, ..., mode='fix') -> AgentResult: ...
    def delete_conversation(self, conversation_id) -> None: ...
    def stop_all_conversations(self) -> None: ...
```

`@runtime_checkable` so backends opt in by matching the methods —
no required base class.

## What is NOT on the contract

The streaming session protocol (long-lived process, NDJSON events,
in-flight permission asks) is **Claude-specific** and lives on
`claude_core_lib`'s `StreamingClaudeSession` directly. OpenHands
has no equivalent (its runtime model is HTTP RPC); forcing both to
share a streaming surface would either warp the contract or strand
OpenHands. Keeping streaming off the Protocol respects the real
asymmetry.

## Consumers

| Package | How it uses this |
|---|---|
| [`claude_core_lib`](../claude_core_lib/) | `ClaudeCliClient` implements `AgentProvider` |
| [`openhands_core_lib`](../openhands_core_lib/) | `KatoClient` implements `AgentProvider` |
| [`agent_core_lib`](../agent_core_lib/) | factory returns the contract type |
| (future) `codex_core_lib` | will implement `AgentProvider` |

## Tests

```
agent_provider_contracts/agent_provider_contracts/tests/
```

Drift guards: pin the eight required methods, the runtime-check
behaviour for compliant + missing-method backends, and DTO
default + immutability invariants.
