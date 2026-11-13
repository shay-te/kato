# Agent Backend — kato

Kato can drive its implementation, testing, and review-fix work through one of two agent backends, and offers a chat with four agent CLIs (see "Chat CLIs" below).

## Choosing an Agent Backend

Selection is a single environment variable:

```env
# default
KATO_AGENT_BACKEND=openhands

# OR
KATO_AGENT_BACKEND=claude
```

- `openhands` (default) drives the OpenHands HTTP server. Uses the `OPENHANDS_*` block of `.env`. The Docker Compose stack still ships an `openhands` container by default.
- `claude` drives Anthropic's Claude Code CLI locally with `claude -p` (non-interactive print mode). Uses the `KATO_CLAUDE_*` block of `.env`. The CLI must be installed and authenticated on the host that runs Kato (`claude login`); the OpenHands container is not required.

Everything that works with OpenHands also works with `claude -p`:

- Implementation conversations per task.
- Optional testing-validation conversations (controlled by `OPENHANDS_SKIP_TESTING`).
- Review-comment fix conversations on existing pull requests, including session resume so the agent keeps context across review rounds (mapped to `claude --resume <session_id>`).
- Repository scope, security guardrails, and the `pr_description.md` PR-description handoff are identical in both backends.

Switching is one env value: change `KATO_AGENT_BACKEND`, run `kato doctor`, restart Kato.

## Setting Up the Claude CLI Backend

```env
KATO_AGENT_BACKEND=claude

# Path to the binary (default: claude on PATH).
KATO_CLAUDE_BINARY=claude

# Optional model override; leave empty to use the CLI's configured default.
# Examples: claude-opus-4-7 | claude-sonnet-4-6 | claude-haiku-4-5-20251001
KATO_CLAUDE_MODEL=

# Optional turn cap, allow/deny tool lists, permission mode.
KATO_CLAUDE_MAX_TURNS=
KATO_CLAUDE_ALLOWED_TOOLS=
KATO_CLAUDE_DISALLOWED_TOOLS=
# When true, kato runs Claude with `--permission-mode bypassPermissions`.
# When false (default), kato uses acceptEdits and routes permission asks
# back through the planning UI.
KATO_CLAUDE_BYPASS_PERMISSIONS=false

# Per-task subprocess timeout (seconds) and an optional startup smoke test.
KATO_CLAUDE_TIMEOUT_SECONDS=1800
KATO_CLAUDE_MODEL_SMOKE_TEST_ENABLED=false
```

Notes:

- Install Claude Code: https://docs.claude.com/en/docs/claude-code/setup
- Authenticate once interactively (`claude login`) on the host. Kato runs the CLI with `-p`, which uses the credentials stored by `claude login`.
- The CLI runs locally and edits files directly in the prepared task branch, so the orchestration layer does not need OpenHands credentials, the agent-server image, or the dedicated testing container when this backend is active. The `OPENHANDS_*` block of `.env` can stay empty.
- `KATO_CLAUDE_PERMISSION_MODE` defaults to `bypassPermissions` because the orchestration layer pins the agent to a prepared branch and runs unattended. Use `acceptEdits` if you would rather have Claude prompt for tool grants in interactive setups.
- The CLI is invoked with `--output-format json` so the orchestration parses `result` and `session_id` from the structured output. Review-comment follow-ups pass that `session_id` back via `--resume`.
- The agent writes `pr_description.md` into the **task folder** (`<workspaces_root>/<task_id>/`), beside the repo clones rather than inside one, and the publication flow reads it as the pull request description — same as the OpenHands path. The location is the guarantee: a file outside every worktree cannot be staged, so it can never reach a PR as a committed file. It used to be `validation_report.md` in the repository root, which depended on the orchestrator stripping it before each push — a blanket `git add -A` in the "merge default branch" WIP commit defeated that permanently, and one report rode three commits into a pull request. A repo-root `validation_report.md` is still stripped if an older prompt produces one, and its text is used only when the task folder has none.

## Chat CLIs: Codex, Grok and OpenCode

Beside the main agent, every task has a chat tab per CLI — **Claude**,
**Codex**, **Grok** (xAI's Grok Build, `grok`) and **OpenCode** (`opencode`,
any provider). Each tab is its own conversation with its own history, resumed
after a restart. A tab whose CLI is missing opens a setup panel with the
install and login commands instead of a chat. They are chat tabs, not the main
agent (`KATO_AGENT_BACKEND` stays `claude` or `openhands`).

Each has its own Settings tab (Codex agent / Grok agent / OpenCode agent),
on by default (`KATO_CODEX_ENABLED`, `KATO_GROK_ENABLED`,
`KATO_OPENCODE_ENABLED` — a restart applies a change):

| | Codex | Grok | OpenCode |
|---|---|---|---|
| Install | `npm install -g @openai/codex` | `curl -fsSL https://x.ai/cli/install.sh \| bash` | `npm install -g @opencode/cli` (or `opencode-ai`, V1) |
| Sign in | `codex login` | `grok login` or `XAI_API_KEY` | `opencode auth login` |
| A turn runs | in Codex's `workspace-write` sandbox | inside Grok's workspace sandbox (Seatbelt / Landlock; Windows: edits only) | with kato's permission rules: edits allowed, nothing outside the task folder |
| Strict git | the sandbox keeps `.git` read-only | **enforced** as `--deny` rules | **enforced** as shell rules |
| Settings | binary, model, effort, timeout, smoke test | binary, model, effort, max turns, allow/deny rules, timeout, smoke test | binary, model (`provider/model`), effort (a model variant), timeout, smoke test |
| Bypass switch | yes (Sandbox tab) | none — no Docker sandbox image; bypass is refused | none — same |

All four transports are mirrors of each other (the same modules, names and
signatures — see `agent_provider_contracts/cli_transport_layout.py`); Codex,
Grok and OpenCode share one per-turn base in `agent_core_lib`. Their own
READMEs (`codex_core_lib/`, `grok_core_lib/`, `opencode_core_lib/`) list the
CLI flags each run uses and what was verified against the CLI's source.

The agent is designed to:

1. Read tasks assigned to it from the configured issue platform.
   Supported issue platforms are YouTrack, Jira, GitHub Issues, GitLab Issues, and Bitbucket Issues.
   `kato.issue_platform` defaults to `youtrack` when unset.
   Only tasks assigned to the configured assignee and currently in one of the configured `issue_states` are eligible.
   When loading a task, the agent also reads issue comments, text attachments, and screenshot attachment metadata so OpenHands gets more complete context.
2. Read each task definition.
3. Ask OpenHands to implement the required changes.
4. Create one pull request per affected repository.
5. Add the aggregated pull request summary back to the configured issue platform, move the issue to the configured review state, and send a review-ready email.
6. Listen to pull request comments and trigger follow-up fixes.
