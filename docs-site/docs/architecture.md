---
sidebar_position: 6
title: Architecture
---

# Architecture

Kato is a monorepo of mostly self-contained libraries with one orchestrator and
a web UI. The design goal: each capability is a black-box library that depends
only on stdlib/third-party (plus one shared agent-behavior base), so the system
stays decoupled and testable.

## The pieces

- **`kato_core_lib`** — the orchestrator. Owns the scan loop, task pre-flight
  (resolve repos → clone workspaces → prep branches), publishing (push + PR), and
  review-comment handling. It wires product-specific text into the agent clients.
- **`agent_core_lib`** — the shared, product-agnostic agent-behavior base
  (prompt helpers, session/text utils). The agent transports import it.
- **Agent transports** — `claude_core_lib`, `codex_core_lib`, `openhands_core_lib`.
  One per backend; the UI looks the same regardless.
- **Provider libs** — `youtrack`, `jira`, `github`, `gitlab`, `bitbucket`, plus
  `git_core_lib` and `provider_client_base` (shared issue/PR client base).
- **Webserver** — a Flask + React app: the planning UI, the approval pipeline,
  and the live Action Guard policy.

Every library keeps its own tests and 100% coverage, and never imports a peer
transport/provider — the orchestrator is the only place glue lives.

## How a task runs

```
scan → get assigned tickets (API)
     → pre-flight: resolve repos, clone per-task workspace, checkout branch
     → agent session (streaming) — you watch the live diff + can comment
     → you click "Done — Push":
         → push branch, open PR per repo
         → move ticket to "In Review", post a summary
         (if no repo changed → status NO_CHANGES, ticket stays put)
```

## How a PR review comment runs

```
scan → new PR comments on "In Review" PRs
     → is it just a question?
         yes → agent answers → reply with a "no code changed" disclaimer
         no  → agent fixes → did it change anything?
                  no  → reply "no changes", leave the thread open
                  yes → push → leave the "addressed" reply for the reviewer
```

Kato never auto-resolves a remote review thread — it posts the reply and leaves
the close to a human.

## Safety invariants

- No auto-commit, no auto-push, no auto-PR, no auto-resolve.
- Kato never auto-deletes a task, record, or workspace; review clones are
  cleanup-protected.
- A forgotten task stays forgotten until you re-adopt it.
- The agent's session sandbox (`--add-dir` set) is fixed at spawn; widening it
  requires a restart, never a live mutation.
