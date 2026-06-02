# agent-core-lib

Reusable prompt, context, guardrail, and safety helpers for coding and
automation agents.

This library owns the generic work that happens before a prompt is sent
to an agent backend or LLM provider: prompt scaffolding, workspace scope
boundaries, checked-in conventions, architecture/lessons context,
review-comment framing, resume snapshots, result normalization, and
credential/phishing detection.

It is product-agnostic. It does not know about Kato's ticketing system,
UI, repository workflow, or model provider.

## Responsibilities

- **Prompt preparation** — reusable scaffolding an agent sees before a
  backend sends work to a model.
- **Safety guardrails** — generic instructions for handling untrusted
  task text, comments, logs, and attachments.
- **Workspace & repository scope** — strict "only read/edit these
  paths" boundaries, and which repos/branches are in scope, without
  encoding any product workflow.
- **Convention injection** — discover/render `AGENTS.md`, architecture
  docs, and a learned-lessons file into the prompt.
- **Review context** — file/line/commit localization and prior-thread
  context for review-comment fix prompts.
- **Conversation continuity** — guidance that helps an agent trust
  existing history instead of repeating expensive reads.
- **Session-id + result normalization** — one canonical session-id
  representation and normalized result helpers.
- **Output-side safety scan** — detective credential/phishing scan over
  the agent's final response, with redacted log previews.
- **Credential pattern bank** — shared high-confidence credential and
  operator-phishing detectors used by agent outputs and sandbox
  workspace scans.
- **Resume snapshots** — render a generic markdown snapshot so another
  agent can continue from recent conversation state.
- **Caller guidance hook** — accept optional caller-provided guidance
  while keeping product-specific text outside this library.

## Non-responsibilities

- **Agent backend factory.** Backend selection and composition live in
  `agent_backend_core_lib`.
- **Provider contracts.** `AgentProvider` and its DTOs live in
  `agent_provider_contracts`.
- **Backend transport.** Claude/Codex/OpenHands process, streaming,
  permission, and sandbox details live in their backend libraries.
- **Raw LLM API calls.** Bedrock/OpenAI/Anthropic request construction
  and response normalization belong in an LLM connection library.
- **Product workflow.** Ticketing, repo publishing, PR/review
  orchestration, schedulers, and UI belong to the host application.
- **Product-specific prompt text.** Guidance tied to a product is
  passed in by the caller, never hardcoded here.

## Prompt-helper example

```python
from agent_core_lib.agent_core_lib.helpers.agent_prompt_utils import (
    security_guardrails_text,
    workspace_scope_block,
)

block = workspace_scope_block(['/abs/path/to/task/workspace'])

block = workspace_scope_block(
    ['/abs/path/to/task/workspace'],
    extra_refusal_guidance='To widen scope: <your product-specific steps>',
)

guardrails = security_guardrails_text()
```

## Agent workflow integration

Use this library in the caller/workflow layer, before invoking an agent
backend or LLM connection:

```python
from agent_core_lib.agent_core_lib.helpers.agent_prompt_utils import (
    security_guardrails_text,
    workspace_scope_block,
)

prompt = '\n\n'.join([
    workspace_scope_block(allowed_paths),
    security_guardrails_text(),
    task_prompt,
])

result = llm_connection.complete_text(prompt=prompt, system=system_text)
```

For CLI-style autonomous agents, pair this library with
`agent_backend_core_lib`, which owns backend selection and the
`AgentProvider` factory.

## Configuration

| Env var | Purpose |
|---|---|
| `AGENT_IGNORED_REPOSITORY_FOLDERS` | Comma-separated repo folder names the agent must not touch. |
| `AGENT_WORKSPACES_ROOT` | Named in the scope block as the per-task workspaces root (informational text only). |
| `AGENT_REPOSITORY_ROOT_PATH` | Named in the scope block as the shared source-clones root (informational text only). |

For backward compatibility, `ignored_repository_folder_names()` falls
back to legacy `KATO_IGNORED_REPOSITORY_FOLDERS` when the generic value
is unset. Prefer the generic name for new consumers.

## Development / testing

```bash
python -m unittest discover -s agent_core_lib/agent_core_lib/tests -p "test_*.py"
```

Tests are self-contained and use fake keys, localhost URLs, and fake
model names. The library imports no host application package and no
backend implementation package.
