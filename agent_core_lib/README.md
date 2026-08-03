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

## Comment-prompt payload (use these — do not hand-roll)

Anything that asks an agent to act on a **comment** — a pull-request review
comment, a batch of them, or an in-app diff comment — must assemble its
payload from these helpers. They were previously copy-pasted per prompt
builder, the copies drifted, and the same operator-visible bugs recurred four
times as a result.

```python
from agent_core_lib.agent_core_lib.helpers.agent_prompt_utils import (
    comment_target_line,             # 1-based commented line, either record shape
    review_comment_location_text,    # 'File: path:line (type)' [+ 'Commit: sha']
    commented_code_block,            # the actual code at that line, framed
    comment_thread_text,             # prior turns, self-replies dropped, framed
    review_comments_batch_text,      # numbered list for a 2+-comment batch
    narrow_edit_guardrails_text,     # "smallest possible change" rules
)
```

Why each is mandatory rather than optional:

- **`commented_code_block`** — a comment saying only "revert this" carries no
  information without the line it points at. Given a file path and a line
  *number*, an agent guesses which change is meant, and that guess overshoots:
  the reported case rewrote an entire file.
- **`narrow_edit_guardrails_text`** — the instruction to stay narrow. One
  builder never had it at all, which is what made the above possible.
- **`comment_thread_text`** — pass `drop_prefixes=` with the prefixes your bot
  uses on its own replies, or the agent is handed its own previous output as
  though a human wrote it and re-reads it as new instructions.
- **`review_comment_location_text`** — one localization vocabulary, so an agent
  is told *where* a comment lives the same way on every surface.

`wrap=` is a parameter, not an import: comment threads and repo file content
are written by whoever can comment or commit, so they must be framed as data
rather than instructions — but the framing helper lives in the sandbox library
and this library depends on no other core-lib. **Callers pass their wrapper.**
Omitting it silently removes a prompt-injection defense; the batch renderer
lost it that way for every multi-comment review.

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
