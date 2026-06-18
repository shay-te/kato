---
sidebar_position: 3
title: Get started
---

# Get started

Kato runs locally and opens a planning UI in your browser. You'll need
**Python 3**, **Git**, and a coding-agent CLI on PATH (the `claude` CLI for the
default Claude backend).

## 5-minute start

```bash
git clone <this-repo>
cd kato

kato bootstrap     # one-time: Python venv + dependencies
kato configure     # interactive wizard for your .env (ticket platform, repos, LLM)
kato doctor        # checks your config is valid
kato up            # starts kato + opens the planning UI in your browser
```

`kato` is the single operator entry point. Other handy subcommands:

```bash
kato test          # run the test suite
kato doctor        # validate connections, credentials, sandbox readiness
```

## What you configure

The `kato configure` wizard writes a `.env` with:

- **Ticket platform** — YouTrack / Jira / GitHub / GitLab / Bitbucket, plus the
  `assignee` Kato scans for. (Leave it as your bot user, or `me`/`currentUser()`
  — Kato resolves your real identity so the @-mention filter still works.)
- **Repositories** — either an explicit list, or a `REPOSITORY_ROOT_PATH` that
  Kato walks to auto-discover `.git` folders. You approve which repos Kato may
  touch from the UI.
- **Agent backend** — `claude` (default), `codex`, or `openhands`, plus the model
  alias (`opus` / `sonnet` / `haiku`) which always tracks the latest.

Settings made in the UI persist to `~/.kato/settings.json`; `.env` is the
read-only fallback (precedence: shell env > `settings.json` > `.env`).

## How a task flows

1. Kato scans for tickets assigned to its bot user.
2. It clones each repo into an isolated per-task workspace and prepares a branch.
3. The agent implements the change; you watch the **live diff** and can comment
   to steer it.
4. When you're happy, click **Done — Push** — Kato pushes the branch, opens the
   PR, and moves the ticket to *In Review*. (Kato never pushes on its own.)

Next: [Approvals](./approvals) and [Security](./security).
