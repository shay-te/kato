# kato-core-lib

Kato's orchestration core. Owns the autonomous task-execution
loop, the per-task workspace lifecycle, the operator-facing
planning UI's server side, and the integration glue between
ticket platforms (`task_core_lib`), version-control providers
(`repository_core_lib`), the Claude CLI runtime, and the
sandboxed agent boundary (`sandbox_core_lib`).

## What kato actually does

A single-sentence model: **kato watches a ticket queue, provisions
an isolated workspace per task, runs an LLM agent against it
under a hardened sandbox, and pushes a pull request when the
agent reports done.**

The orchestration loop — assigned-task scan → REP gate →
workspace clone → preflight → planning session → publish →
review-comment fixes → finish — lives here in `data_layers/`.
The Claude CLI wrapper, streaming session manager, and event
log live under `client/claude/`. The Flask + React planning UI
that lets operators chat into a live agent lives in
[`webserver/`](../webserver/).

## Package layout

```
kato_core_lib/
├── kato_core_lib.py              ← composition root (assembles services)
├── main.py                       ← daemon entry + startup preflight
├── configure_project.py          ← interactive .env generator
├── validate_env.py               ← `./kato doctor`
├── client/
│   ├── claude/                   ← Claude CLI wrapper, streaming session
│   ├── openhands/                ← OpenHands backend (alternative to Claude)
│   └── ticket_client_base.py
├── comment_core_lib/             ← local JSON-backed comment store (kato-internal)
├── config/                       ← Hydra config defaults
├── data_layers/
│   ├── data/                     ← Task, ReviewComment, repository models
│   ├── data_access/              ← persistence layer
│   └── service/                  ← AgentService, RepositoryService, TaskPublisher,
│                                   ReviewCommentService, WorkspaceManager,
│                                   PlanningSessionRunner, …
├── helpers/                      ← shared utilities (file cache, dotenv loader, audit log,
│                                   text utils, retry, mission logging, …)
├── jobs/                         ← cron-style background jobs
├── templates/email/              ← email notification templates
└── validation/                   ← startup validators (REP, branch publishability, …)
```

## What lives elsewhere (and why)

These are sibling core-libs because they have a clear
self-contained boundary and are reusable outside kato:

| Package | Boundary |
|---|---|
| [`sandbox_core_lib`](../sandbox_core_lib/) | Hardened Docker sandbox + protection layers (audit chain, TLS pin, credential leak detection). Security boundary. |
| [`task_core_lib`](../task_core_lib/) | Ticket-platform abstraction; resolves to YouTrack / Jira / GitHub Issues / GitLab / Bitbucket. |
| [`repository_core_lib`](../repository_core_lib/) | Pull-request abstraction; resolves to GitHub / GitLab / Bitbucket. |
| [`youtrack_core_lib`](../youtrack_core_lib/), [`jira_core_lib`](../jira_core_lib/), [`github_core_lib`](../github_core_lib/), [`gitlab_core_lib`](../gitlab_core_lib/), [`bitbucket_core_lib`](../bitbucket_core_lib/) | Per-provider implementations sitting behind the two abstractions above. |

The intentionally kato-internal extraction — `comment_core_lib` —
lives **inside** this package because it has no consumer outside
kato; pulling it out as a sibling would be premature.

## Running

| Command | What it does |
|---|---|
| `./kato up` | Boot the daemon (autonomous scan + planning UI server). |
| `./kato configure` | Interactive `.env` generator. |
| `./kato doctor` | Validate every config key the active mode needs. |
| `./kato test` | Run the kato test suite + every owned core-lib's tests. |
| `./kato approve-repo` | Repository approval picker (REP gate). |
| `./kato history` | Recent autonomous-task activity. |

See [`AGENTS.md`](../AGENTS.md) for contribution rules,
[`SECURITY.md`](../SECURITY.md) for the security model, and
[`sandbox_core_lib/SANDBOX_PROTECTIONS.md`](../sandbox_core_lib/SANDBOX_PROTECTIONS.md)
for the sandbox attack-map + residuals.

## Tests

```
tests/                                                   ← kato tests
sandbox_core_lib/sandbox_core_lib/tests/                 ← sandbox tests (run alongside)
webserver/tests/                                         ← Flask app tests
webserver/ui/src/**/*.test.js                            ← React UI tests
```

`./kato test` runs the python suites; `(cd webserver/ui && npm test)`
runs the UI suite.
