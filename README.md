<p align="center">
  <img src="./kato.png" alt="Kato" width="220" />
</p>

# Kato

<p align="center">
  <img src="./docs/img/bruce-lee-kato.jpg" alt="Bruce Lee as Kato in The Green Hornet (1966)" width="180" />
  <br />
  <em>Kato will help you kick all your tasks.</em>
</p>

Welcome to Kato! This repository is structured as a [`core-lib`](https://shay-te.github.io/core-lib/) application and follows the documented `core-lib` package layout.

> **🛡 Security layers.** Three gates run before any agent touches a
> repository:
>
> - **Repository denylist** (`KATO_REPOSITORY_DENYLIST`) — repos
>   matched against this list are dropped from kato's inventory at
>   load time. There is no override; if a repo id is on the denylist,
>   kato will not see it.
> - **Pre-execution security scanner** — every per-task workspace
>   clone is scanned by `detect-secrets`, `bandit`, `safety`, `npm
>   audit`, and a `.env`-file checker before the agent spawns. Real
>   secrets / CVEs / dangerous patterns block the task with a ticket
>   comment.
> - **Restricted Execution Protocol (REP)** — kato refuses to run
>   an agent against any repository the operator hasn't explicitly
>   approved. Run `./kato approve-repo` to open the picker — it
>   shows every repo kato can find with `[x]` next to the ones
>   already approved; toggle indices, press Enter to apply. REP is
>   always on. There is no off switch. New repos start in
>   `restricted` mode; the operator elevates to `trusted` after
>   reviewing the first agent run.
>
> Combined, these catch the most common breach patterns in
> small-team codebases — committed `.env` files, vulnerable deps,
> and misrouted task tags.

## Why Kato

The name comes from Kato, the Green Hornet's sidekick, famously played by Bruce Lee. That makes it a fitting name for this project: a helper that works alongside the main mission, stays useful in the background, and helps get important work done.

I love and respect Bruce Lee, and I wanted the name to reflect that admiration.

## Why Core-Lib

`core-lib` is a strong fit for this project because this agent is not just a script that calls one API. It has to coordinate issue platforms, repository providers, OpenHands, jobs, configuration, persistence, notifications, and testing in one place without collapsing into one large pile of glue code.

Why it works especially well here:

- `core-lib` gives the project a clean layered shape: clients for external APIs, data-access wrappers for boundaries, services for orchestration, and jobs for entrypoints. That maps directly to what this agent does every day.
- `core-lib` is built around a central application library object, which is exactly what this project needs. `KatoCoreLib` can be initialized once and reused from the CLI, scheduled jobs, and tests instead of rebuilding the application's wiring in multiple places.
- The `core-lib` docs emphasize fast setup, consistent structure, and reusable runtime wiring. That matters here because this project has to compose several providers cleanly: issue systems, repository systems, OpenHands, and notifications.
- `core-lib` keeps configuration-driven behavior first-class. That is one of the main reasons this repo can support multiple source issue platforms without pushing provider-specific branching into the orchestration layer.
- `core-lib` is very test-friendly. This project depends on many external systems, so confidence comes from isolating boundaries and mocking them cleanly. The layered `core-lib` structure makes that practical.
- `core-lib` reduces framework churn. Instead of spending time on custom bootstrapping, connection management, configuration loading, and lifecycle glue, this repository can stay focused on the agent's actual behavior.
- `core-lib` is an especially good choice here because it was designed by the same author for this exact style of application: modular, integration-heavy Python services that need to stay readable as they grow.

For this codebase, that means `core-lib` is not just a dependency. It is part of the design strategy. It gives the project a stable foundation, lets new providers fit an existing pattern, and keeps the repository centered on agent behavior rather than plumbing.

Reference:
- https://shay-te.github.io/core-lib/
- https://shay-te.github.io/core-lib/advantages.html

## Choosing an Agent Backend

Kato can drive its implementation, testing, and review-fix work through one of two agent backends. Selection is a single environment variable:

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
- Repository scope, security guardrails, and the `validation_report.md` PR-description handoff are identical in both backends.

Switching is one env value: change `KATO_AGENT_BACKEND`, run `make doctor`, restart Kato.

### Setting Up the Claude CLI Backend

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
- The agent still produces `validation_report.md` in the repository root; the existing publication flow uses it as the pull request description and removes it before pushing — same as the OpenHands path.

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

## Structure

```text
kato_core_lib/
  client/                        # external services kato talks to
    agent_client.py              # AgentClient Protocol — the contract
    retrying_client_base.py      # shared retry / HTTP plumbing
    pull_request_client_*.py     # cross-provider PR abstraction
    ticket_client_*.py           # cross-provider issue abstraction
    bitbucket/                   # Bitbucket auth + PR + issues
    github/                      # GitHub PR + issues
    gitlab/                      # GitLab PR + issues
    jira/                        # Jira issues
    youtrack/                    # YouTrack issues
    claude/                      # Claude Code CLI backend
      cli_client.py              #   one-shot autonomous client
      streaming_session.py       #   long-lived planning subprocess
      session_manager.py         #   per-task session registry + persistence
    openhands/                   # OpenHands HTTP backend (kato_client.py)
    openrouter/                  # OpenRouter helpers (used by openhands)
  config/
    kato_core_lib.yaml
  data_layers/
    data/                        # YouTrack / git / agent value types
    data_access/                 # raw fetch + parse layer
    service/                     # orchestration: scan → plan → execute
      agent_service.py           #   top-level loop, tag handling
      task_preflight_service.py  #   resolve repos, prep branches
      task_publisher.py          #   commit, push, open PR
      planning_session_runner.py #   route to streaming Claude
      review_comment_service.py  #   handle PR review feedback
  helpers/                       # cross-cutting *_utils.py modules
  validation/                    # startup + per-task safety checks
  jobs/process_assigned_tasks.py # the cron-scheduled scan loop
  main.py                        # process entrypoint
  kato_core_lib.py               # core-lib wiring: builds AgentService
webserver/                       # planning UI (Flask + React)
  kato_webserver/
    app.py                       #   Flask routes (SSE + POST)
    git_diff_utils.py            #   tree / diff for the right pane
    session_registry.py          #   in-memory tab list (legacy)
  templates/index.html           # HTML shell
  static/css/app.css             # dark-theme styles
  static/js/app.js               # vanilla-JS chat + SSE + status bar
  ui/                            # Vite + React source for the right pane
    src/{App,FilesTab,ChangesTab}.jsx
scripts/
  bootstrap.sh                   # Mac/Linux first-time setup
  bootstrap.ps1                  # Windows PowerShell equivalent
tests/
  config/config.yaml             # test fixture config
```

### Architecture at a glance

```text
                       ┌──────────────────────────────┐
                       │  YouTrack / Jira / GitHub …  │
                       │   (issues, comments, tags)   │
                       └──────────────┬───────────────┘
                                      │ poll
                                      ▼
                  ┌────────────────────────────────────┐
                  │  kato.main  ─  ProcessAssignedTasks │
                  │   30s scan loop, signal handling   │
                  └──────────────┬─────────────────────┘
                                 │
                                 ▼
              ┌──────────────────────────────────────────┐
              │              AgentService                │
              │  • wait-planning short-circuit (chat tab)│
              │  • TaskPreflightService (resolve+prep)   │
              │  • runner OR one-shot client (implement) │
              │  • TestingService (validate)             │
              │  • TaskPublisher (commit / push / PR)    │
              └──────────────┬───────────────────────────┘
                             │
                ┌────────────┴────────────┐
                ▼                         ▼
   ┌─────────────────────┐   ┌────────────────────────────┐
   │  ClaudeCliClient    │   │     KatoClient (OpenHands) │
   │  (one-shot -p)      │   │     HTTP API client        │
   └──────────┬──────────┘   └────────────────────────────┘
              │ also used by:
              ▼
   ┌─────────────────────────────────────────────────────┐
   │           PlanningSessionRunner                     │
   │  uses ClaudeSessionManager + StreamingClaudeSession │
   │  (long-lived `claude -p --input-format stream-json` │
   │   subprocess, one per task id, persisted records)   │
   └──────────┬──────────────────────────────────────────┘
              │ shared in-memory ↕ persisted on disk
              ▼
   ┌─────────────────────────────────────────────────────┐
   │      Planning UI webserver (daemon thread)          │
   │  Flask + SSE  →  vanilla JS  +  React right-pane    │
   │  • tab list, chat, permission modal                 │
   │  • Files / Changes tabs (git tree + diff)           │
   │  • status bar (kato logger → ring buffer → SSE)     │
   │  • browser notifications on key events              │
   └─────────────────────────────────────────────────────┘
```

Key invariants:

* **One workspace folder per task.** Each ticket id (`PROJ-12`) gets
  `~/.kato/workspaces/PROJ-12/` with fresh clones of every repo its
  `kato:repo:*` tags name. Two parallel tasks against the same repo are
  physically isolated checkouts — no shared branch state, no cross-task
  git races. Sized by `KATO_MAX_PARALLEL_TASKS`.
* **One subprocess per task id.** `ClaudeSessionManager` keyed on the
  ticket id; `--resume` keeps context across kato restarts.
* **The orchestrator and the webserver share the same managers.**
  `WorkspaceManager` (tab list source of truth) and
  `ClaudeSessionManager` (live subprocess + chat events) live in one
  Python process so the planning UI sees both in real time without IPC.
* **Workspace lifecycle = ticket state.** Workspaces are created when
  kato starts a task, persist across restarts via `.kato-meta.json`,
  and are deleted when the ticket leaves the Open + Review states
  (e.g. PR merged → Done).
* **Single-threaded gate, multi-threaded execute.** The scan loop
  pulls tasks from the ticket system one at a time; heavy execution
  (clone, run agent, test, publish) fans out across a thread pool.

## How It Works

This project follows the `core-lib` layering on purpose:

- `KatoCoreLib` wires the app once at startup, builds the clients, data-access objects, and services, and validates the external connections before work starts.
- `client/` contains provider-specific API code for issue platforms, repository providers, and OpenHands.
- `data_layers/data_access/` stays focused on boundary work such as ticket updates and pull-request API calls.
- `data_layers/service/` owns the business workflow. This is where task selection, state transitions, repository preparation, OpenHands runs, publishing, notifications, and review-comment handling live.

That separation matters because the service flow should read like the real agent workflow. Kato starts by validating configuration and external access, then repeats one scan loop: process assigned tasks first, then process pull-request review comments. Tasks and comments are processed sequentially, one after the other, so repository state from one item does not leak into the next one.

### Highlight Summary

- Startup validates `.env`, repository access, the active issue platform, the main OpenHands server, and the testing OpenHands server unless testing is skipped.
- The scan loop waits for the configured startup delay, scans assigned tasks, then scans review comments, then sleeps until the next scan.
- The task-fix flow reads the task, prepares clean branches, opens OpenHands implementation and testing conversations, commits and pushes changes, opens pull requests, moves the task to review, and stores pull-request context for follow-up comments.
- The review-comment fix flow scans review pull requests, skips already-handled comment threads, opens an OpenHands review-fix conversation, pushes the branch update, replies to the reviewer, resolves the comment when supported, and records the processed comment keys.
- Failed repository, branch, push, publish, and state-transition checks stop the unsafe part of the workflow instead of marking work as done too early.

### Startup Flow

1. `python -m kato_core_lib.main`, `make run`, or the Docker entrypoint loads Hydra config and values from `.env`.
2. Environment validation runs before the application is built. Missing required values fail fast.
3. `KatoCoreLib` builds the active issue-platform client, repository service, OpenHands implementation service, OpenHands testing service, notification service, task publisher, preflight service, and review-comment service.
4. Startup dependency validation checks repository connections, the active issue-platform connection, the main OpenHands connection, and the testing OpenHands connection unless `OPENHANDS_SKIP_TESTING=true`.
5. After startup succeeds, the job loop waits for `OPENHANDS_TASK_SCAN_STARTUP_DELAY_SECONDS`.
6. Each loop cycle runs task processing first and review-comment processing second.
7. If a cycle fails, the error is logged and the loop retries after `OPENHANDS_TASK_SCAN_INTERVAL_SECONDS`.

### Task Fix Flow

For each eligible assigned task, the service does these checks and steps:

1. Skip the task if it was already processed during this run.
2. Validate model access for the task before spending work on repository changes.
3. Check whether an earlier blocking comment still prevents a retry.
4. Read the full task context, including issue comments, supported text attachments, and screenshot attachment metadata.
5. Infer the affected repositories from the task summary and description.
6. Validate that every repository is available locally, on the expected destination branch, and clean before starting work.
7. Build the task branch name for each repository and prepare those branches locally.
8. Before OpenHands starts, fetch `origin` and rebase any existing local task branch on top of `origin/<branch>` when that remote branch exists.
9. Validate that task branches can be pushed.
10. Move the issue to the in-progress state and add a started comment.
11. Open the implementation conversation in the main OpenHands server.
12. Validate that the task branches contain publishable changes.
13. Open the testing conversation in the configured testing OpenHands server, or skip it when `OPENHANDS_SKIP_TESTING=true`.
14. Commit and push the branch updates, then create pull requests or merge requests through the repository provider API.
15. Add the pull-request summary back to the task.
16. If every repository published successfully, move the task to the configured review state, mark the task processed for this run, and send the completion notification.
17. Remember the pull-request context so later review comments can be mapped back to the correct repository, branch, task, and OpenHands session.

If any repository cannot be published, the successful pull requests are kept, the task is not moved to the review state, and the failure is reported clearly instead of being hidden.

### Review Comment Fix Flow

After task processing, the agent checks tracked review pull requests for unseen comments:

0. Before polling comments, compare the current review-state task list against all tasks with tracked pull-request contexts. For any task that is no longer in the review state (merged, moved to done, or closed by the reviewer), Kato deletes its OpenHands conversation so the agent-server container is stopped and removed. On normal process shutdown (SIGTERM / SIGINT), all remaining conversations are also deleted.
1. Look only at pull requests that belong to tasks already moved into the review state.
2. Load or reconstruct the saved pull-request context for the repository, branch, task, and OpenHands session.
3. Fetch pull-request comments from the repository provider.
4. Build the full review-comment thread context for OpenHands.
5. Skip comment threads already replied to by Kato, already processed in memory, or already covered by another comment with the same resolution target.
6. Log `Working on pull request comments: <pull request name>` before logging the concrete comment id.
7. Prepare the same working branch again by fetching `origin` and rebasing the local branch on `origin/<branch>` before the review-fix conversation starts.
8. Open the review-fix conversation in OpenHands with the pull request comment and the saved task context. The saved session ID from the original implementation conversation is passed as the parent so the agent-server container is reused for context and cost efficiency.
9. Publish the review fix back to the same branch. If git push is still rejected because the remote branch changed while OpenHands was working, Kato fetches `origin/<branch>`, rebases once, and retries the push.
10. Reply to the original review comment with the OpenHands result.
11. Resolve the review comment when the provider supports it.
12. If the provider reports the comment is already resolved or unavailable, Kato logs a warning and continues because the fix was already published and replied.
13. Mark both the visible comment id and the provider resolution target as processed so the same thread is not handled again in the same run.
14. If the review-comment flow fails, restore repository branches before the failure is raised.

### Testing OpenHands Routing

Implementation always uses the main OpenHands server from `OPENHANDS_BASE_URL`.

Testing uses:

- the dedicated testing server from `OPENHANDS_TESTING_BASE_URL` when `OPENHANDS_TESTING_CONTAINER_ENABLED=true`
- the main `OPENHANDS_BASE_URL` when `OPENHANDS_TESTING_CONTAINER_ENABLED=false`
- no testing conversation at all when `OPENHANDS_SKIP_TESTING=true`

When the testing container is enabled and `OPENHANDS_SKIP_TESTING=false`, `make compose-up` starts Docker Compose with the `testing` profile so the extra `openhands-testing` service is available. When it is disabled, no dedicated testing server is started and the agent keeps testing on the main OpenHands instance. When `OPENHANDS_SKIP_TESTING=true`, the agent skips the validation step entirely and `make compose-up` stays on the normal profile even if the dedicated testing container is enabled.

## Required Environment

For the shortest local setup path, use the interactive configurator:

```bash
make bootstrap
make configure
make doctor
make run
```

`make configure` runs `python scripts/generate_env.py --output .env` and writes a first-pass `.env` for you. It asks:

- where your tasks live
- where your source code lives
- which issue states should be processed
- which review state and field should be used
- the first repository, OpenHands, and optional email settings

The configurator uses the same style of shell prompts used by `core-lib`, so the setup flow stays consistent with the rest of the stack.

If you prefer to edit the file manually, start here:

```bash
cp .env.example .env
```

Use `KATO_ISSUE_PLATFORM` for all new setups.

## Tag reference

Kato uses ticket-platform tags (YouTrack tags / Jira labels / GitHub labels / GitLab labels) namespaced under `kato:` to control per-task behavior. Apply or remove them on the ticket itself; kato reads them on every scan tick and reacts on the next pass.

| Tag | What it does |
|---|---|
| `kato:repo:<repo-name>` | **Required for any task that should produce a PR.** Names the repository folder (under `REPOSITORY_ROOT_PATH`) that kato should clone for this task. Add multiple tags to drive a multi-repo task — one PR per tag. The folder name must match the directory; case-sensitive. |
| `kato:wait-planning` | **Don't run autonomously — open a chat tab.** Kato registers the task in the planning UI and waits for the operator to chat with the agent. No implementation, no testing, no PR. Remove the tag to hand control back to the orchestrator. |
| `kato:wait-before-git-push` | **Run the agent, but pause before push + PR.** Kato runs implementation and testing as usual, commits to the local task branch, then stops. The operator approves the push via the planning UI's "Approve push" button (or by removing the tag and re-triggering the task). The push and PR creation are still done by kato — never by Claude. |
| `kato:triage:investigate` | **Classify the task instead of working it.** Kato spends one read-only Claude turn analyzing the task description and writes back exactly one `kato:triage:<level>` outcome tag (see below), then removes this tag. No code edits, no PR. Useful for triaging a backlog. |
| `kato:triage:critical` | Outcome: real, urgent. Set by the triage flow. |
| `kato:triage:high` | Outcome: real, work soon. |
| `kato:triage:medium` | Outcome: real, normal priority. |
| `kato:triage:low` | Outcome: real, low priority. |
| `kato:triage:duplicate` | Outcome: covered by another ticket. |
| `kato:triage:wontfix` | Outcome: real but won't be worked. |
| `kato:triage:invalid` | Outcome: not a real issue. |
| `kato:triage:needs-info` | Outcome: not enough info to act on. |
| `kato:triage:blocked` | Outcome: blocked by something external. |
| `kato:triage:question` | Outcome: a question, not a task. |

**Cross-platform tag mutation.** Native APIs are used where available (YouTrack, Jira, GitHub Issues, GitLab Issues). Platforms without native tag support (Bitbucket Issues today) fall through to a structured comment-marker fallback — kato posts `<!-- kato-tag {"action": "add", "tag": "..."} -->` as a comment.

## Third-Party Setup

Pick one issue platform with `KATO_ISSUE_PLATFORM`, then fill in the matching block below. Keep the other issue-platform blocks empty unless you are switching providers or using their repository API credentials for pull requests.

After editing `.env`, run:

```bash
make doctor
```

### Setting Up YouTrack

Use this when tasks are coming from YouTrack:

```env
KATO_ISSUE_PLATFORM=youtrack
YOUTRACK_API_BASE_URL=https://your-company.youtrack.cloud
YOUTRACK_API_TOKEN=...
YOUTRACK_PROJECT=PROJ
YOUTRACK_ASSIGNEE=your-youtrack-login
YOUTRACK_ISSUE_STATES=Todo,Open
YOUTRACK_PROGRESS_STATE_FIELD=State
YOUTRACK_PROGRESS_STATE=In Progress
YOUTRACK_REVIEW_STATE_FIELD=State
YOUTRACK_REVIEW_STATE=To Verify
```

`YOUTRACK_ISSUE_STATES` is the queue Kato scans. The progress and review state settings tell Kato how to move the issue when work starts and when the pull request is ready.

### Setting Up Jira

Use this when tasks are coming from Jira:

```env
KATO_ISSUE_PLATFORM=jira
JIRA_API_BASE_URL=https://your-company.atlassian.net
JIRA_API_TOKEN=...
JIRA_EMAIL=you@example.com
JIRA_PROJECT=PROJ
JIRA_ASSIGNEE=assignee-account-id-or-username
JIRA_ISSUE_STATES=To Do,Open
JIRA_PROGRESS_STATE_FIELD=status
JIRA_PROGRESS_STATE=In Progress
JIRA_REVIEW_STATE_FIELD=status
JIRA_REVIEW_STATE=In Review
```

`JIRA_API_TOKEN` is the API token. Keep `JIRA_EMAIL` set for Atlassian authentication flows that need the account email.

### Setting Up GitHub Issues

Use this when tasks are coming from GitHub Issues:

```env
KATO_ISSUE_PLATFORM=github
GITHUB_API_BASE_URL=https://api.github.com
GITHUB_API_TOKEN=...
GITHUB_OWNER=owner-or-org
GITHUB_REPO=repo-name
GITHUB_ASSIGNEE=assignee-login
GITHUB_ISSUE_STATES=open
GITHUB_PROGRESS_STATE_FIELD=labels
GITHUB_PROGRESS_STATE=In Progress
GITHUB_REVIEW_STATE_FIELD=labels
GITHUB_REVIEW_STATE=In Review
```

`GITHUB_API_TOKEN` is also used for GitHub git push and pull request creation when discovered repositories live on GitHub.

### Setting Up GitLab Issues

Use this when tasks are coming from GitLab Issues:

```env
KATO_ISSUE_PLATFORM=gitlab
GITLAB_API_BASE_URL=https://gitlab.com/api/v4
GITLAB_API_TOKEN=...
GITLAB_PROJECT=group/project
GITLAB_ASSIGNEE=assignee-username
GITLAB_ISSUE_STATES=opened
GITLAB_PROGRESS_STATE_FIELD=labels
GITLAB_PROGRESS_STATE=In Progress
GITLAB_REVIEW_STATE_FIELD=labels
GITLAB_REVIEW_STATE=In Review
```

`GITLAB_API_TOKEN` is also used for GitLab git push and merge request creation when discovered repositories live on GitLab.

### Setting Up Bitbucket Issues

Use this when tasks are coming from Bitbucket Issues:

```env
KATO_ISSUE_PLATFORM=bitbucket
BITBUCKET_API_BASE_URL=https://api.bitbucket.org/2.0
BITBUCKET_API_TOKEN=...
BITBUCKET_USERNAME=bitbucket-username
BITBUCKET_API_EMAIL=you@example.com
BITBUCKET_WORKSPACE=workspace
BITBUCKET_REPO_SLUG=repo-slug
BITBUCKET_ASSIGNEE=assignee-username
BITBUCKET_ISSUE_STATES=new,open
BITBUCKET_PROGRESS_STATE_FIELD=state
BITBUCKET_PROGRESS_STATE=open
BITBUCKET_REVIEW_STATE_FIELD=state
BITBUCKET_REVIEW_STATE=resolved
```

`BITBUCKET_API_TOKEN` is used for Bitbucket git auth and REST API calls. `BITBUCKET_API_EMAIL` is required for Bitbucket pull request API auth.

### Setting Up OpenHands With Bedrock

Use this when `OPENHANDS_LLM_MODEL` starts with `bedrock/`:

```env
OH_SECRET_KEY=stable-random-local-secret
OPENHANDS_LLM_MODEL=bedrock/your-model-id
OPENHANDS_LLM_API_KEY=
OPENHANDS_LLM_BASE_URL=
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_REGION_NAME=us-west-2
AWS_SESSION_TOKEN=
AWS_BEARER_TOKEN_BEDROCK=
```

For Bedrock auth, choose one path:

- Standard AWS credentials: set `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and `AWS_REGION_NAME`. Set `AWS_SESSION_TOKEN` too when the credentials are temporary. Leave `AWS_BEARER_TOKEN_BEDROCK` empty.
- Bedrock bearer token: set `AWS_BEARER_TOKEN_BEDROCK`. Leave `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION_NAME`, and `AWS_SESSION_TOKEN` empty.

### Setting Up OpenHands With OpenRouter

Use this when `OPENHANDS_LLM_MODEL` starts with `openrouter/`:

```env
OH_SECRET_KEY=stable-random-local-secret
OPENHANDS_LLM_MODEL=openrouter/openai/gpt-4o-mini
OPENHANDS_LLM_API_KEY=...
OPENHANDS_LLM_BASE_URL=https://openrouter.ai/api/v1
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_REGION_NAME=
AWS_SESSION_TOKEN=
AWS_BEARER_TOKEN_BEDROCK=
```

OpenRouter requires both `OPENHANDS_LLM_API_KEY` and `OPENHANDS_LLM_BASE_URL`. Leave the AWS Bedrock variables empty for OpenRouter runs.

## Environment Reference

The list below mirrors `.env.example`.

### Ticket And Repository

| Variable | What it does |
| --- | --- |
| `KATO_ISSUE_PLATFORM` | Selects the active issue platform. Supported values are `youtrack`, `jira`, `github`, `gitlab`, and `bitbucket`. |
| `KATO_AGENT_BACKEND` | Selects the active agent backend. Supported values are `openhands` (default) and `claude`. |
| `YOUTRACK_API_BASE_URL` | YouTrack API base URL. |
| `YOUTRACK_API_TOKEN` | YouTrack API token. |
| `YOUTRACK_PROJECT` | YouTrack project key used to fetch tasks. |
| `YOUTRACK_ASSIGNEE` | YouTrack assignee to scan for tasks. |
| `YOUTRACK_PROGRESS_STATE_FIELD` | YouTrack field used for the in-progress transition. |
| `YOUTRACK_PROGRESS_STATE` | YouTrack value used for the in-progress transition. |
| `YOUTRACK_REVIEW_STATE_FIELD` | YouTrack field used for the review transition. |
| `YOUTRACK_REVIEW_STATE` | YouTrack value used for the review transition. |
| `YOUTRACK_ISSUE_STATES` | YouTrack issue states that qualify for processing. |
| `JIRA_API_BASE_URL` | Jira API base URL. |
| `JIRA_API_TOKEN` | Jira API token. |
| `JIRA_EMAIL` | Jira user email for authentication. |
| `JIRA_PROJECT` | Jira project key used to fetch tasks. |
| `JIRA_ASSIGNEE` | Jira assignee to scan for tasks. |
| `JIRA_PROGRESS_STATE_FIELD` | Jira field used for the in-progress transition. |
| `JIRA_PROGRESS_STATE` | Jira value used for the in-progress transition. |
| `JIRA_REVIEW_STATE_FIELD` | Jira field used for the review transition. |
| `JIRA_REVIEW_STATE` | Jira value used for the review transition. |
| `JIRA_ISSUE_STATES` | Jira issue states that qualify for processing. |
| `GITHUB_API_BASE_URL` | GitHub Issues API base URL. |
| `GITHUB_API_TOKEN` | GitHub API token. Also used for GitHub git push and PR creation when needed. |
| `GITHUB_OWNER` | GitHub repository owner used to scope issues. |
| `GITHUB_REPO` | GitHub repository name used to scope issues. |
| `GITHUB_ASSIGNEE` | GitHub assignee to scan for tasks. |
| `GITHUB_PROGRESS_STATE_FIELD` | GitHub Issues field used for the in-progress transition. |
| `GITHUB_PROGRESS_STATE` | GitHub Issues value used for the in-progress transition. |
| `GITHUB_REVIEW_STATE_FIELD` | GitHub Issues field used for the review transition. |
| `GITHUB_REVIEW_STATE` | GitHub Issues value used for the review transition. |
| `GITHUB_ISSUE_STATES` | GitHub Issues states that qualify for processing. |
| `GITLAB_API_BASE_URL` | GitLab Issues API base URL. |
| `GITLAB_API_TOKEN` | GitLab API token. Also used for GitLab git push and merge request creation when needed. |
| `GITLAB_PROJECT` | GitLab project path used to scope issues. |
| `GITLAB_ASSIGNEE` | GitLab assignee to scan for tasks. |
| `GITLAB_PROGRESS_STATE_FIELD` | GitLab Issues field used for the in-progress transition. |
| `GITLAB_PROGRESS_STATE` | GitLab Issues value used for the in-progress transition. |
| `GITLAB_REVIEW_STATE_FIELD` | GitLab Issues field used for the review transition. |
| `GITLAB_REVIEW_STATE` | GitLab Issues value used for the review transition. |
| `GITLAB_ISSUE_STATES` | GitLab Issues states that qualify for processing. |
| `BITBUCKET_API_BASE_URL` | Bitbucket Issues API base URL. |
| `BITBUCKET_API_TOKEN` | Bitbucket API token. Used as the password for Bitbucket git auth and Bitbucket REST API auth. |
| `BITBUCKET_USERNAME` | Bitbucket username used for git push auth. |
| `BITBUCKET_API_EMAIL` | Atlassian account email used for Bitbucket REST API auth with API tokens. |
| `BITBUCKET_WORKSPACE` | Bitbucket workspace used to scope issues. |
| `BITBUCKET_REPO_SLUG` | Bitbucket repository slug used to scope issues. |
| `BITBUCKET_ASSIGNEE` | Bitbucket assignee to scan for tasks. |
| `BITBUCKET_PROGRESS_STATE_FIELD` | Bitbucket Issues field used for the in-progress transition. |
| `BITBUCKET_PROGRESS_STATE` | Bitbucket Issues value used for the in-progress transition. |
| `BITBUCKET_REVIEW_STATE_FIELD` | Bitbucket Issues field used for the review transition. |
| `BITBUCKET_REVIEW_STATE` | Bitbucket Issues value used for the review transition. |
| `BITBUCKET_ISSUE_STATES` | Bitbucket Issues states that qualify for processing. |
| `REPOSITORY_ROOT_PATH` | Root folder where the agent scans for checked-out repositories. |
| `MOUNT_DOCKER_DATA_ROOT` | Host folder that holds all Docker bind-mounted data under one parent directory. |
| `KATO_IGNORED_REPOSITORY_FOLDERS` | Comma-separated folder names to exclude from repository auto-discovery. |

### Kato Runtime

| Variable | What it does |
| --- | --- |
| `OPENHANDS_BASE_URL` | Base URL for the primary OpenHands server. |
| `OPENHANDS_API_KEY` | API key for the primary OpenHands server. |
| `OPENHANDS_SKIP_TESTING` | Skips the testing validation conversation and publishes after implementation. |
| `OPENHANDS_TESTING_CONTAINER_ENABLED` | Enables the optional dedicated testing OpenHands container. |
| `OPENHANDS_TESTING_BASE_URL` | Base URL for the dedicated testing OpenHands server. |
| `OPENHANDS_TESTING_PORT` | Host port used for the optional testing container. |
| `OPENHANDS_CONTAINER_LOG_ALL_EVENTS` | Enables all OpenHands event logging inside the `openhands` container. |
| `KATO_LOG_LEVEL` | Log level for the agent app process. |
| `KATO_WORKFLOW_LOG_LEVEL` | Log level for workflow-specific logs. |
| `OPENHANDS_POLL_INTERVAL_SECONDS` | Delay between Kato conversation polling attempts. |
| `OPENHANDS_MAX_POLL_ATTEMPTS` | Maximum number of times the agent waits for an Kato conversation result. |
| `OPENHANDS_TASK_SCAN_STARTUP_DELAY_SECONDS` | Delay before the agent starts scanning for tasks after startup. |
| `OPENHANDS_TASK_SCAN_INTERVAL_SECONDS` | Delay between task scan cycles. |
| `KATO_FAILURE_EMAIL_ENABLED` | Enables failure notification emails. |
| `KATO_FAILURE_EMAIL_TEMPLATE_ID` | Template id used for failure notification emails. |
| `KATO_FAILURE_EMAIL_TO` | Recipient address for failure notification emails. |
| `KATO_FAILURE_EMAIL_SENDER_NAME` | Sender name for failure notification emails. |
| `KATO_FAILURE_EMAIL_SENDER_EMAIL` | Sender email for failure notification emails. |
| `KATO_COMPLETION_EMAIL_ENABLED` | Enables completion notification emails. |
| `KATO_COMPLETION_EMAIL_TEMPLATE_ID` | Template id used for completion notification emails. |
| `KATO_COMPLETION_EMAIL_TO` | Recipient address for completion notification emails. |
| `KATO_COMPLETION_EMAIL_SENDER_NAME` | Sender name for completion notification emails. |
| `KATO_COMPLETION_EMAIL_SENDER_EMAIL` | Sender email for completion notification emails. |
| `EMAIL_CORE_LIB_SEND_IN_BLUE_API_KEY` | SendInBlue API key used by `email-core-lib`. |
| `SLACK_WEBHOOK_URL_ERRORS_EMAIL` | Slack webhook used by `email-core-lib` error reporting. |

The `openhands` container reuses the same `OPENHANDS_LLM_*` and `AWS_*` values from the shared `.env` file, so the Bedrock configuration is defined once. `OPENHANDS_CONTAINER_LOG_ALL_EVENTS` is the only service-specific override for that container.

### OpenHands Container

| Variable | What it does |
| --- | --- |
| `OPENHANDS_PORT` | Host port exposed for the OpenHands container. |
| `OPENHANDS_LOG_LEVEL` | OpenHands container log level. |
| `OH_SECRET_KEY` | OpenHands secret key used to persist secrets safely across restarts. |
| `OPENHANDS_STATE_DIR` | Host path for OpenHands state storage. |
| `OPENHANDS_WEB_URL` | Public URL that OpenHands should advertise. |
| `OPENHANDS_RUNTIME` | Runtime backend used by OpenHands. |
| `KATO_AGENT_SERVER_IMAGE_REPOSITORY` | Agent server image repository used by the OpenHands container. |
| `KATO_AGENT_SERVER_IMAGE_TAG` | Agent server image tag used by the OpenHands container. |
| `OPENHANDS_SSH_AUTH_SOCK_HOST_PATH` | Host SSH agent socket path forwarded into Docker for SSH git remotes. |

### OpenHands LLM

| Variable | What it does |
| --- | --- |
| `OPENHANDS_LLM_MODEL` | Primary OpenHands model name. |
| `OPENHANDS_LLM_API_KEY` | API key for the primary OpenHands model. |
| `OPENHANDS_LLM_BASE_URL` | Optional custom base URL for the primary OpenHands model. OpenRouter typically uses `https://openrouter.ai/api/v1`. |
| `OPENHANDS_MODEL_SMOKE_TEST_ENABLED` | Runs an extra startup model smoke test during connection validation. |
| `OPENHANDS_TESTING_LLM_MODEL` | Model name used by the dedicated testing OpenHands server. |
| `OPENHANDS_TESTING_LLM_API_KEY` | API key used by the dedicated testing OpenHands server. |
| `OPENHANDS_TESTING_LLM_BASE_URL` | Base URL used by the dedicated testing OpenHands server. OpenRouter testing models should use `https://openrouter.ai/api/v1`. |
| `OPENHANDS_LLM_API_VERSION` | Optional API version passed through to the OpenHands LLM config. |
| `OPENHANDS_LLM_NUM_RETRIES` | Optional LLM retry count passed through to OpenHands. |
| `OPENHANDS_LLM_TIMEOUT` | Optional LLM timeout passed through to OpenHands. |
| `OPENHANDS_LLM_DISABLE_VISION` | Optional OpenHands flag to disable vision features. |
| `OPENHANDS_LLM_DROP_PARAMS` | Optional OpenHands flag for dropping unsupported model params. |
| `OPENHANDS_LLM_CACHING_PROMPT` | Optional caching prompt passed through to OpenHands. |
| `AWS_ACCESS_KEY_ID` | AWS access key for Bedrock-backed models or AWS auth in Docker. |
| `AWS_SECRET_ACCESS_KEY` | AWS secret key for Bedrock-backed models or AWS auth in Docker. |
| `AWS_REGION_NAME` | AWS region used for Bedrock-backed models or AWS auth in Docker. |
| `AWS_SESSION_TOKEN` | Optional AWS session token for temporary Bedrock credentials. |
| `AWS_BEARER_TOKEN_BEDROCK` | Bedrock bearer token alternative to AWS access keys. |

### Claude CLI Backend

| Variable | What it does |
| --- | --- |
| `KATO_CLAUDE_BINARY` | Path to (or PATH name of) the Claude Code CLI binary. Defaults to `claude`. |
| `KATO_CLAUDE_MODEL` | Optional model id passed via `--model` (e.g. `claude-opus-4-7`). Empty uses the CLI default. |
| `KATO_CLAUDE_MAX_TURNS` | Optional cap on agent turns per task, passed via `--max-turns`. Empty means no cap. |
| `KATO_CLAUDE_EFFORT` | Optional reasoning depth passed via `--effort` (`low`/`medium`/`high`/`xhigh`/`max`). Empty leaves Claude on its built-in default. |
| `KATO_CLAUDE_ALLOWED_TOOLS` | Comma-separated allowlist passed via `--allowedTools`. |
| `KATO_CLAUDE_DISALLOWED_TOOLS` | Comma-separated denylist passed via `--disallowedTools`. |
| `KATO_CLAUDE_BYPASS_PERMISSIONS` | When `true`, kato runs Claude with `--permission-mode bypassPermissions` (no per-tool prompts) inside the hardened Docker sandbox. When `false` (the default), kato runs in `acceptEdits` mode and routes any permission asks back over the planning UI. Refused under root, refused under CI/Docker/cron (no TTY for confirmation), and double-prompted on the terminal at every interactive startup. See [BYPASS_PROTECTIONS.md](BYPASS_PROTECTIONS.md). |
| `KATO_CLAUDE_TIMEOUT_SECONDS` | Per-task subprocess timeout. Defaults to 1800. Minimum 60. |
| `KATO_CLAUDE_MODEL_SMOKE_TEST_ENABLED` | Runs a small `claude -p` prompt during startup validation. Off by default. |
| `KATO_ARCHITECTURE_DOC_PATH` | Optional path to a project-architecture markdown file. When set, kato appends the file's contents to Claude's system prompt on every spawn (autonomous, planning, chat-respawn) via `--append-system-prompt`. Re-read on each spawn so edits land without a kato restart. |
| `KATO_TASK_PUBLISH_MAX_RETRIES` | Retries for the publish step (per-repo PR creation + the move-to-review transition). Implementation work is not re-run. Defaults to `2` (up to 3 attempts) with exponential backoff. |
| `KATO_WORKSPACE_REVIEW_TTL_SECONDS` | How long a workspace in `review` state persists before the cleanup loop deletes it, regardless of whether the ticket is still in the review bucket. Default `3600` (1 hour). Set to `0` to disable TTL-based cleanup (legacy behavior: workspace persists until the ticket leaves both assigned and review). Review-comment processing for cleaned tickets re-clones on demand. |

The active issue provider comes from `kato.issue_platform`, which defaults to `youtrack`.
Issue states can be configured directly in `.env` with `YOUTRACK_ISSUE_STATES`, `JIRA_ISSUE_STATES`, `GITHUB_ISSUE_STATES`, `GITLAB_ISSUE_STATES`, and `BITBUCKET_ISSUE_STATES`.
The review-state target also comes from the active provider config:
- YouTrack uses `kato.youtrack.review_state_field` and `kato.youtrack.review_state`.
- Jira uses `kato.jira.review_state_field` and `kato.jira.review_state`.
- GitHub Issues uses `kato.github_issues.review_state_field` and `kato.github_issues.review_state`.
- GitLab Issues uses `kato.gitlab_issues.review_state_field` and `kato.gitlab_issues.review_state`.
- Bitbucket Issues uses `kato.bitbucket_issues.review_state_field` and `kato.bitbucket_issues.review_state`.
Processed task state, processed review-comment ids, and pull-request comment context are kept in memory during a run so the agent can skip already-completed work and poll for new review comments without writing local state.
If email notifications are enabled, install the optional dependency set with `python -m pip install -e ".[notifications]"`.
The email body text comes from [`completion_email.j2`](kato_core_lib/templates/email/completion_email.j2) and [`failure_email.j2`](kato_core_lib/templates/email/failure_email.j2), rendered with Jinja2 template variables at runtime.
The Hydra config is registered through [`hydra_plugins/kato/kato_searchpath.py`](hydra_plugins/kato/kato_searchpath.py), so standard Hydra overrides work. Example:

```bash
python -m kato_core_lib.main kato.retry.max_retries=7
```

### Open Source Notes

This project is meant to be usable by other teams, so a few things are worth calling out up front:

- `make configure` is the easiest way to create a first `.env`, and `.env.example` is the canonical template.
- Never commit real secrets. Keep `.env` local, and only use `.env.example` for documentation and defaults.
- The workflow is split on purpose:
  - OpenHands edits files in the task branch.
  - orchestration handles commit, push, pull request creation, and branch restoration.
- Before task work starts, the agent runs a model-access preflight for the configured OpenHands model(s), so invalid Bedrock or OpenRouter credentials fail fast before implementation begins.
- Testing behavior is controlled by separate flags:
  - `OPENHANDS_SKIP_TESTING=true` skips the validation conversation entirely.
  - `OPENHANDS_TESTING_CONTAINER_ENABLED=true` enables the dedicated testing OpenHands container.
  - `OPENHANDS_MODEL_SMOKE_TEST_ENABLED=false` only disables the startup smoke test.
- If you change `.env`, recreate the containers so Docker Compose reloads the new values.
- Bedrock-backed models may need `AWS_SESSION_TOKEN` in addition to the AWS access key and secret when temporary credentials are used.
- SSH git remotes require `SSH_AUTH_SOCK` to be mounted correctly.
- `clean.sh` is destructive and is intended for local cleanup only. It removes Docker containers and prunes unused Docker resources without prompting.

### Troubleshooting

If something does not work as expected, the most common checks are:

1. Run `docker compose config` and confirm the rendered values match the working configuration.
2. Recreate the containers after changing `.env`.
3. Confirm the repository workspace is on the destination branch after a failure or after cleanup.
4. Check whether the active issue platform and repository provider are both configured in `.env`.
5. Verify that the OpenHands model credentials match the provider you selected.

Common failure modes:

- Bedrock authentication errors usually mean the AWS key, secret, region, or session token is wrong or stale.
- Branch-publish failures usually mean the task branch never got a committed change or the repo could not be restored cleanly.
- Dirty worktree errors mean the task branch still has uncommitted edits and the workspace needs cleanup before the next run.
- Missing git permissions usually mean the host repository path or SSH auth socket is not mounted the way the container expects.

### Supported Providers

The agent currently supports these issue trackers:

- YouTrack
- Jira
- GitHub Issues
- GitLab Issues
- Bitbucket Issues

The repository provider is inferred from the configured repository metadata, and the same task can span multiple repositories if the task text matches them.

## How To Use

### Full First-Run Checklist

If a developer is starting from zero, these are the steps:

1. Clone the repository.
2. Change into the repository directory.
3. Run `make bootstrap`.
4. Run `make configure` to create `.env`, or copy `.env.example` to `.env` and edit it manually.
5. Fill in or confirm the credentials for the selected issue platform.
6. Fill in or confirm the first repository entry credentials and local path.
7. Add more repository entries in the config file if tasks can span multiple repos.
8. Fill in or confirm OpenHands server settings.
9. Fill in or confirm OpenHands LLM provider settings.
10. Fill in email settings if notifications are enabled.
11. Decide whether to run locally or with Docker Compose.
12. Validate the environment values.
13. Start the application.
14. Confirm the agent can connect to the configured issue platform, OpenHands, and every configured repository.

What is automated now:

- `./scripts/bootstrap.sh`
  - creates `.env` from `.env.example` if needed
  - creates `.venv` if needed
  - installs the project
  - runs the tests
- `make configure`
  - asks which issue platform holds your tasks
  - asks which platform hosts your code
  - can scan a projects folder for git repositories
  - asks which issue states and review state should be used
  - writes `.env` for the root repository path and OpenHands setup
- `make doctor`
  - validates agent and OpenHands env vars
  - exits non-zero if required values are missing, so it can be used in CI or pre-flight scripts
- `make run`
  - loads `.env`
  - starts the app
- Docker entrypoint
  - waits for OpenHands
  - starts the app

Still manual:

- filling real secrets in `.env`
- choosing the LLM/provider/model
- choosing whether to use local run or Docker
- adding extra repository entries directly in YAML when a task can span multiple repositories

### Quick Commands

1. Bootstrap the repo:

```bash
make bootstrap
```

2. Create `.env` interactively:

```bash
make configure
```

3. Validate config:

```bash
make doctor
```

`make doctor` returns a non-zero exit code on validation failure.

4. Run locally:

```bash
make run
```

5. Or run with Docker:

```bash
make compose-up
```

`make compose-up` brings the Compose stack up in the background and then attaches
directly to the `kato` container TTY, so inline countdowns and rotating status
spinners render in place instead of being flattened into prefixed Compose log lines.

### Manual Flow

1. Install the project dependencies in your environment.

```bash
pip install -e .
```

2. Fill `.env` instead of exporting variables one by one. Start from `.env.example` and update the values you need there.

3. Adjust `kato_core_lib/config/kato_core_lib.yaml` only if you need settings beyond what `.env` exposes, such as extra repositories or retry tuning via `KATO_EXTERNAL_API_MAX_RETRIES`. Issue states, review columns, and review-ready email recipients can now be configured directly in `.env`.

```yaml
kato:
  issue_platform: youtrack
  retry:
    max_retries: 5
  failure_email:
    enabled: true
    template_id: "42"
    body_template: failure_email.j2
    recipients:
      - ops@example.com
  completion_email:
    enabled: true
    template_id: "77"
    body_template: completion_email.j2
    recipients:
      - reviewers@example.com
  youtrack:
    review_state_field: State
    review_state: In Review
    issue_states:
      - Todo
      - Open
  jira:
    review_state_field: status
    review_state: In Review
    issue_states:
      - To Do
      - Open
  github_issues:
    review_state_field: labels
    review_state: In Review
    issue_states:
      - open
  gitlab_issues:
    review_state_field: labels
    review_state: In Review
    issue_states:
      - opened
  bitbucket_issues:
    base_url: https://api.bitbucket.org/2.0
    token: BITBUCKET_API_TOKEN
    username: BITBUCKET_USERNAME
    api_email: BITBUCKET_API_EMAIL
    workspace: your-workspace
    repo_slug: issues-repo
    review_state_field: state
    review_state: resolved
    issue_states:
      - new
      - open
```

4. Load `.env` in your shell and run the agent.

```bash
set -a
source .env
set +a
python -m kato_core_lib.main
```

### Docker Compose

You can also run OpenHands and this agent together with Docker Compose:

```bash
docker compose up --build
```

If you want the Kato inline spinner and countdown UI to render correctly, prefer
`make compose-up` over raw `docker compose up --build`, because the Make target
attaches directly to the `kato` container terminal.

What the compose stack does:

- starts an `openhands` container on port `3000`
- builds and starts an `kato` container from this repo
- makes the agent wait until OpenHands is reachable at `http://openhands:3000`
- then runs `python -m kato_core_lib.main`

The compose file uses the current official OpenHands container image pattern from the OpenHands docs:

- https://docs.openhands.dev/openhands/usage/run-openhands/local-setup
- https://github.com/OpenHands/OpenHands

Before running `docker compose up --build`, make sure `.env` contains the selected issue-platform settings, repository settings, OpenHands settings, retry settings, and optional email settings you want Docker Compose to pass through.
Docker Compose uses `REPOSITORY_ROOT_PATH` as the host source path and mounts it into both the agent container and the OpenHands sandbox at `/workspace/project`, so Docker runs use the same in-container workspace path consistently. The agent mount must stay writable because the agent itself performs git preflight, branch checkout, and fast-forward pulls there before delegating implementation work.
All Docker bind-mounted runtime data lives under `MOUNT_DOCKER_DATA_ROOT` (default `./mount_docker_data`) in service-specific subfolders such as `openhands/` and `openhands_state/`.

If you use `.env`, Docker Compose will load it automatically, so you can keep both the agent config and the OpenHands LLM config in one place and avoid manual setup in the OpenHands UI for the env-supported options. The `openhands` service also reads its logging and model defaults from the same file.
The OpenHands container always stores its internal state at `/.openhands`; `OPENHANDS_STATE_DIR` only controls which host folder is mounted there, so prefer an absolute host path when overriding it. By default, the host side lives under `MOUNT_DOCKER_DATA_ROOT/openhands_state/`.

OpenHands behavioral rules are also supported from this repo through [`AGENTS.md`](AGENTS.md). That lets you keep coding/testing instructions in the project instead of configuring them manually in OpenHands.

What happens when it runs:

- It fetches only tasks assigned to the configured issue-platform assignee.
- It ignores tasks that are not in the configured `issue_states`.
- It enriches the task context with issue comments, text attachment contents, and screenshot attachment references when the selected platform exposes them.
- It retries transient client failures up to `kato.retry.max_retries`.
- If the overall run fails, it sends failure notifications through `email-core-lib` to the configured recipients.
- For each eligible task, it infers the affected repositories, asks OpenHands to implement the work across that scoped workspace set, opens one pull request per repository, comments the aggregated PR summary back to the configured issue platform, moves the issue to the configured review state when all repositories succeed, and sends a completion email that asks for review.
- After task processing, it polls tracked pull requests for new review comments, passes the full accumulated review-comment context into OpenHands for each unseen comment, and records processed comment ids so the same comment is not reprocessed on the next run.

### Partial Failure Behavior

If a task spans multiple repositories and one pull request succeeds while another fails, the agent does not roll back the successful pull request. Instead it:

- posts the partial pull-request summary back to the configured issue platform
- records the failed repository ids in the run result
- leaves the issue out of the review state transition
- sends the failure notification path with the failing repositories in the error text

That behavior is deliberate: the agent prefers explicit partial visibility over trying to revert repository state automatically.

## Planning UI Build & Cleanup

The right-pane planning UI is a Vite + React app that compiles to a single bundle served by the Flask webserver. The Python side reads the prebuilt files from `webserver/static/build/` at runtime — there is no live transpile step in production.

### Building the bundle

```bash
cd webserver/ui
npm install        # first run only
npm run build      # outputs webserver/static/build/app.{js,css}
```

`npm run build` is idempotent and finishes in ~1s. There is also `npm run dev` if you want Vite's hot-reload while iterating on the UI; it serves on a separate port and proxies through to the Flask backend.

After a rebuild, **hard-refresh the browser** (Cmd+Shift+R on macOS, Ctrl+Shift+R elsewhere) so it doesn't keep serving the cached `app.js`. That's the most common gotcha when changes don't appear.

### Cleaning between runs

For a normal Ctrl+C → `make compose-up` cycle there is nothing to clean. The table below covers the cases where something does need a wipe.

| What | When to clean | How |
| --- | --- | --- |
| Browser cache | After a UI rebuild looks stale | Cmd+Shift+R (hard refresh) |
| `__pycache__` | Almost never; only if you suspect a stale `.pyc` | `find . -name __pycache__ -prune -exec rm -rf {} +` |
| `webserver/ui/node_modules` | After a `package.json` dependency change misbehaves | `rm -rf webserver/ui/node_modules && (cd webserver/ui && npm install)` |
| `webserver/static/build/` | When a build seems half-applied | `rm -rf webserver/static/build && (cd webserver/ui && npm run build)` |
| Per-task workspaces (`~/.kato/workspaces/`) | To wipe a stuck tab | `rm -rf ~/.kato/workspaces/<task-id>` |
| Session records (`~/.kato/sessions/`) | To forget Claude session ids | `rm -rf ~/.kato/sessions` (the workspace's `.kato-meta.json` re-seeds the id on next boot if it has one) |
| Claude transcripts (`~/.claude/projects/<encoded>/<id>.jsonl`) | To erase chat history replay for a task | Delete the matching JSONL — but you'll lose history-replay for that tab |

`clean.sh` exists for Docker-side cleanup (containers, volumes); it is destructive and prunes unused Docker resources without prompting.

## Security model — note

Kato hands large amounts of trust to the underlying agent (Claude / OpenHands): the agent reads the task description, decides which files to edit, and writes the changes. What kato actually does to contain a misbehaving agent today:

- **Prompt-level guardrails** baked into every kato prompt ([cli_client.py](kato_core_lib/client/claude/cli_client.py)) ask the agent not to touch credentials, escape the repository, or run git commands. These are advisory — a sufficiently determined or compromised model can ignore them.
- **Per-tool permission prompts via the planning UI** when `KATO_CLAUDE_BYPASS_PERMISSIONS=false` (the default). Each Bash / write-style tool call fires a modal that you Approve / Deny by hand, and the decision is sent back to Claude before it can act. This is the real interactive safety layer; it only works when you're watching.
- **Per-task workspace isolation on the filesystem.** Each task gets a fresh clone under `~/.kato/workspaces/<task-id>/`. Two parallel tasks don't share branch state. This is isolation between *tasks*, not between *the agent and your machine*.

What kato does **not** do today:

- Network isolation for the agent (it has the same network access as the host kato process).
- Filesystem sandboxing (the agent can read anything the kato process can).
- Per-task containerization for the agent.

`KATO_CLAUDE_BYPASS_PERMISSIONS=true` removes the planning-UI prompt layer in exchange for unattended speed. To make this state impossible to enable silently or by accident, kato applies the following defense-in-depth layers:

- **Refused under root.** Kato will not start when `KATO_CLAUDE_BYPASS_PERMISSIONS=true` and the process runs as root. There is no exception and no override.
- **Refused under CI / Docker / cron / systemd.** When stdin is not a TTY, kato refuses to start with bypass on — there is no flag-only escape hatch. Acknowledgement must come from a real terminal. Either run kato interactively to confirm, or unset the flag.
- **Double-prompt on every interactive boot.** When stdin is a TTY, kato asks the operator twice with `prompt_yes_no` ("are you sure?" then "final confirmation, this disables every per-tool prompt for the entire session?"). Either no aborts startup. A fat-fingered Enter cannot slip through.
- **Unmissable stderr banner** at every boot, written before logger configuration so log level cannot suppress it.
- **Persistent red banner across the top of the planning UI** — every operator looking at the browser sees the bypass state.
- **Configurator requires typing `I ACCEPT`** before writing the flag (`python -m kato_core_lib.configure_project`).
- **Per-spawn `WARNING` log** on every Claude turn naming the loss of per-tool prompts.

Only enable bypass when you've already locked the agent down at a different layer (devcontainer, dedicated VM, scoped credentials, egress firewall — see SECURITY.md).

The actual safety net is the same one you use for human contributors: **review every diff before merging**. Treat the agent's output as untrusted and gate it through normal code review.

### Recommended sandbox: Claude Code devcontainer

For unattended runs (especially with `KATO_CLAUDE_BYPASS_PERMISSIONS=true`) the recommended isolation layer is [Claude Code's devcontainer](https://code.claude.com/docs/en/devcontainer): run the `claude` binary inside a container with no network and only the per-task workspace mounted in. Kato itself does not yet wire this automatically, but operators can set it up today by configuring `KATO_CLAUDE_BINARY` to a wrapper script that launches `claude` inside the devcontainer. If you are running kato unattended, this is the layer that turns "advisory guardrails" into actual containment.

### Operator responsibilities

By running kato — and especially by setting `KATO_CLAUDE_BYPASS_PERMISSIONS=true` — you, the operator, accept the following:

- **You authorize the agent to act with your credentials.** Anything the kato process can reach (git remotes, ticket platforms, the local filesystem, the network, any environment variable you pass in) is reachable by the agent. There is no internal privilege boundary between kato and the agent it spawns.
- **You are responsible for the systems kato touches.** Kato is intended for use against repositories and ticket platforms you own or are explicitly authorized to modify. Do not point it at third-party systems without that authorization.
- **You are responsible for reviewing the agent's output.** Every PR kato opens must go through normal human code review before merging. The MIT no-warranty disclaimer below covers the maintainers; it does not move review responsibility off the operator.
- **You are responsible for your own sandbox.** If your use case requires network isolation, filesystem sandboxing, secret-scope reduction, or any compliance property (SOC 2, HIPAA, GDPR, export control, etc.), build that layer yourself — devcontainer, separate VM, scoped credentials — before pointing kato at production work.
- **You are responsible for what you set true.** `KATO_CLAUDE_BYPASS_PERMISSIONS=true` and any future flag of similar weight ship off-by-default. Flipping them on is an explicit operator decision, recorded in your `.env`, and surfaced in kato's logs as a `WARNING`. The decision and its consequences are yours.

Vulnerability disclosure path and the longer threat model live in [SECURITY.md](SECURITY.md).

### No warranty

Kato is provided under the [MIT License](LICENSE) — no warranty, express or implied. You run kato on your code, your repos, and your credentials at your own risk. The maintainers do not take responsibility for damage caused by the agent (a compromised model, a misconfigured environment, an exfiltrated secret, a force-pushed branch, anything else). If your use case requires guaranteed isolation or compliance properties, build that layer yourself before pointing kato at production work.

## Testing

From the repository root, install the project in your environment and run the unit test suite with:

```bash
pip install -e .
python3 -m unittest discover -s tests
```

The test suite includes:

- mocked unit tests for the orchestration services, especially `agent_service`, `implementation_service`, `repository_service`, and `testing_service`
- boundary tests for the provider clients and retry helpers
- small integration-style regressions that exercise the task-to-PR workflow shape without hitting live external systems

CI runs the same suite under `coverage` and prints a coverage summary in the job log.

If you only want to run a single test module, use:

```bash
python3 -m unittest discover -s tests -p 'test_notification_service.py'
```

## What This Scaffold Implements

- `core-lib` application wrapper for the agent.
- `core-lib`-style `client`, `data_layers/data`, `data_layers/data_access`, and `data_layers/service` packages.
- Data-access wrappers around issue platforms, OpenHands, and repository provider integrations.
- A service layer that orchestrates the full task-to-PR flow.
- A review-comment processing loop for pull-request review comments.
- A job entrypoint for processing assigned tasks plus a `tests/config` Hydra scaffold.

## Current Limitations

- Real git workspace handling per task.
- Final adaptation to the exact OpenHands API and your issue-platform fields.
- No end-to-end integration test exercises a live issue-platform -> OpenHands -> pull-request provider flow yet.

## Environment Variables Configuration

We use a `.env` file to manage configuration instead of hardcoding values in `docker-compose.yaml`. To set up your environment:

1. Copy the example environment file:
```bash
cp .env.example .env
```

2. Configure your `.env` file with your actual values:
```bash
# Edit .env file with your specific configurations for:
# - Issue platform credentials (YouTrack, Jira, GitHub, GitLab, Bitbucket)
# - Database settings
# - OpenHands configuration
# - LLM settings (including AWS credentials for Bedrock models if needed)
# - Sandbox volumes mapping
```


# Saving costs tips

Use a cheaper main OPENHANDS_LLM_MODEL. This is usually the largest lever.
Lower `kato.retry.max_retries` from 3 to 2 or 3 if your setup is stable.
Keep YOUTRACK_ISSUE_STATES tight so only truly ready tasks get processed.
Batch review feedback into fewer comments, because each review-fix cycle can trigger more OpenHands work.
Keep task context lean: avoid huge pasted logs, long comment threads, and unnecessary attachments.
Keep task and review-comment handling lean so the in-memory workflow stays predictable during a run.
Don’t expect much savings from poll interval tuning; that mostly affects waiting/API chatter, not LLM spend.
