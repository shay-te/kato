# Architecture — kato

This file is a **map of the kato codebase** — what each module owns, how the pieces compose at boot, and the contracts that aren't obvious from reading individual files. It's a navigation aid, not a mirror of the source. When the code says one thing and this doc says another, the code wins; update the doc.

**If you're new here, read the sections in order.** Sections 1–4 give you enough to find any file; sections 5–8 cover the parts most likely to surprise you (lazy inventory, comment-driven blocking, multi-repo task flow, triage short-circuit, recovery, the planning UI's SSE shape).

---

## 1. What kato does

Kato is an unattended agent orchestrator. It scans a ticket platform (YouTrack / Jira / GitHub Issues / GitLab Issues / Bitbucket Issues), picks up tickets assigned to a configured user, runs an agent backend (Claude Code CLI in print mode, or OpenHands) to implement each task inside an isolated per-task workspace clone, then publishes the result as one PR per affected repository. The same scan loop also polls in-flight PRs for new review comments and runs the agent again to address them.

Two pieces run in the same Python process:
- the **scan loop** (`kato.main` → `ProcessAssignedTasksJob` → `AgentService`)
- the **planning webserver** ([`webserver/kato_webserver/app.py`](webserver/kato_webserver/app.py)) — a Flask + SSE app that serves the React planning UI under `webserver/static/`, sharing the in-memory session and workspace managers with the orchestrator so the user can chat with a live agent or watch a turn stream in real time.

```
ticket platform ──poll──▶ scan loop ──▶ AgentService ──▶ preflight → run → publish
                                              │
                                              ├── short-circuit handlers
                                              │   (TriageService, WaitPlanningService)
                                              │
                                              ├── ClaudeSessionManager ◀─ planning webserver ◀─ React UI (SSE)
                                              │
                                              └── WorkspaceManager ── ~/.kato/workspaces/<task-id>/<repo>/
```

Configuration is Hydra + env vars (.env). Two pluggable axes: which **issue platform** (`KATO_ISSUE_PLATFORM`) and which **agent backend** (`KATO_AGENT_BACKEND`). Most cross-cutting behavior is also env-toggled — see [.env.example](.env.example) for the canonical list.

---

## 2. Built on core-lib

Kato is a [`core-lib`](https://shay-te.github.io/core-lib/) application. `core-lib` is a small framework that prescribes an **Onion Architecture** layout for Python service libraries — three data layers (data → data_access → service), plus `client/` for outbound boundaries and `jobs/` for entrypoints — wired through one `CoreLib` subclass that exposes services as attributes. Kato follows that layout exactly, so the `core-lib` [docs](https://shay-te.github.io/core-lib/) and [advantages page](https://shay-te.github.io/core-lib/advantages.html) apply here unchanged. The longer rationale for the choice lives in [README.md](README.md); this section records the binding contract.

### 2.1 The `core-lib` base classes kato extends

| `core-lib` primitive | Kato usage |
|---|---|
| `core_lib.core_lib.CoreLib` | [`KatoCoreLib`](kato/kato_core_lib.py) — the single composition root; subclasses call `super().__init__()` so `core_lib_started` and the cache/observer registries are wired |
| `core_lib.data_layers.service.service.Service` | every class in `kato/data_layers/service/*_service.py` |
| `core_lib.data_layers.data_access.data_access.DataAccess` | [`task_data_access.py`](kato/data_layers/data_access/task_data_access.py), [`pull_request_data_access.py`](kato/data_layers/data_access/pull_request_data_access.py) |
| `core_lib.rule_validator.RuleValidator` / `ValueRuleValidator` | per-field rule checks inside data-access |
| `core_lib.jobs.job.Job` | [`ProcessAssignedTasksJob`](kato/jobs/process_assigned_tasks.py) — the scan-loop entrypoint |
| `core_lib.client.client_base.ClientBase` | [`retrying_client_base.py`](kato/client/retrying_client_base.py) — adds transient-error retry to the stock GET/POST/PUT/DELETE wrapper |
| `core_lib.helpers.command_line` / `core_lib.helpers.validation` | used by [`configure_project.py`](kato/configure_project.py) for the interactive setup flow |
| `email-core-lib` | error-mail notifications wired into [`NotificationService`](kato/data_layers/service/notification_service.py) |

Hydra config is namespaced under `cfg.core_lib.app.*` (logger name, etc.) — see [`config/kato_core_lib.yaml`](kato/config/kato_core_lib.yaml). The `core-lib` convention is to instantiate components from yaml `_target_:` keys via `instantiate_config(self.config.core_lib.<name>)`; kato deviates here and uses **explicit constructor calls** in `_build_agent_service` because the wiring graph is small enough to read top-to-bottom and explicit calls give type-checkers/IDE jump-to-definition on every dependency. The yaml still drives values.

### 2.2 The `core-lib` principles kato follows

These are the ground rules the framework enforces. They aren't decoration; if a change wants to violate one, the change is almost always in the wrong place in the layout.

1. **Onion Architecture — no upward reach.** Each layer can call into the layer below it but never the reverse. The `core-lib` docs spell out three data layers ([data layers doc](https://shay-te.github.io/core-lib/data_layers.html)):
    - **Data layer** (`data_layers/data/`) — entities, connections, migrations. Knows nothing else.
    - **Data Access layer** (`data_layers/data_access/`) — API facade over the data layer. Owns validation (`RuleValidator`), exception handling, and query-shape decisions.
    - **Service layer** (`data_layers/service/`) — business logic, orchestration of data-access + clients, transformation, caching. The bulk of kato's behavior lives here.
   
   Plus the boundary + entrypoint layers:
    - `client/` — outbound API + subprocess wrappers. Implements `ClientBase` (or a domain-specific protocol like `TicketClientBase`). Knows nothing of services.
    - `jobs/` — entrypoints. Calls services. Owns no logic of its own beyond scheduling.

2. **One composition root, services as attributes.** `KatoCoreLib.__init__` is the only place that instantiates concrete services and wires them together; the resulting services are exposed as attributes on the instance (`core_lib.service.publish_task_execution(...)`). Nothing else may know which subclass implements which port. [AGENTS.md](AGENTS.md) spells this out: `kato_core_lib.py` is composition-only — no helpers, no prompt templates, no config-key parsing live there.

3. **Hydra-driven config, env-overrideable.** Anything that varies per environment (which platform, which backend, retry counts, ignore lists, doc paths) goes through `.env` → Hydra → constructor. No `if platform == 'jira'` branches in service code. Boundary swaps happen at the composition root via factory functions (`build_ticket_client`) — the `core-lib` test pattern of overriding `_target_:` in a test yaml to swap a real client for a mock works here too if you ever need it.

4. **Use core-lib primitives instead of forking.** When `core-lib` provides a primitive (retry, rule validation, job lifecycle, command-line prompts, registry pattern, cache decorator, `@ResultToDict`), kato uses it. New helpers go in `kato/helpers/*_utils.py` only when `core-lib` has no equivalent. The framework's stated value is *fewer external dependencies and consistent patterns* — ad-hoc parallel implementations defeat both.

5. **Test against the real CoreLib instance.** `core-lib`'s testing pattern is: build a singleton `CoreLib` once per test run via `sync_create_start_core_lib()` (clears `cache_registry` + `observer_registry` between runs), instantiate **real** `Service` / `DataAccess` subclasses, and swap only the *external* boundary (HTTP, subprocess, filesystem). Mocking `core-lib` itself or its base classes adds noise without coverage. [AGENTS.md](AGENTS.md) forbids it.

6. **Modularity — small libraries that compose.** `core-lib` is designed for splitting larger products into focused libraries; kato is itself one such library, and depends on `email-core-lib` for the notification side. When a slice of kato grows large enough to stand alone (e.g. a generic agent-backend abstraction), the framework's expectation is that you'd extract it into its own `core-lib` library rather than letting it bloat the parent.

A practical consequence: when you're deciding *where* a new piece of code goes, walk down the layers. Outbound HTTP / subprocess / external API → a `client/`. Raw fetch + parse against a boundary → a `DataAccess`. Orchestration of two boundaries with business rules → a `Service`. Scheduled or one-shot entrypoint → a `Job`. If it doesn't fit any of these, it probably belongs in `helpers/` — and if it doesn't fit `helpers/` either, the design is the thing to revisit, not the layout.

### 2.3 `core-lib` primitives kato does and doesn't use today

`core-lib` ships more primitives than kato currently leans on. Knowing which are wired in vs. available helps when you're tempted to invent something parallel.

**In use:**
- `CoreLib`, `Service`, `DataAccess`, `Job`, `ClientBase` (the layout backbone — see §2.1).
- `RuleValidator` / `ValueRuleValidator` for typed field rules in data-access.
- `helpers.command_line` for the configurator's interactive prompts.

**Available but not currently used** (reach for these before writing a new helper):
- **`@Cache` + `CacheRegistry`** ([cache doc](https://shay-te.github.io/core-lib/cache.html)) — RAM / Memcached / Redis / NoCache handlers. Kato has no cross-process cache today; if a future feature needs one (e.g. per-process inventory cache that survives task boundaries), use this rather than a custom dict.
- **`@ResultToDict`** ([result_to_dict doc](https://shay-te.github.io/core-lib/result_to_dict.html)) — serialize SQLAlchemy/temporal/nested types to plain dicts. Kato uses dataclasses for value types and doesn't currently need this; it'd be the right tool if a service starts returning ORM objects to a JSON response layer.
- **`Registry` / `DefaultRegistry`** ([registry doc](https://shay-te.github.io/core-lib/registry.html)) — typed key→object registry. Use this if kato gains another pluggable axis (e.g. multiple notification channels keyed by name).
- **`JobScheduler` + `initial_delay`/`frequency` config** ([job doc](https://shay-te.github.io/core-lib/job.html)) — kato today runs its own sleep-spinner loop in `main.py` because it needs signal-handler integration; if that constraint relaxes, the stock scheduler is the right replacement.
- **`instantiate_config(...)`** — yaml-driven object construction via `_target_:` keys. Useful if the wiring graph grows enough that explicit constructor calls in `_build_agent_service` become hard to read.
- **`CRUDDataAccess` / `CRUDSoftDeleteDataAccess`** ([crud doc](https://shay-te.github.io/core-lib/crud_data_access.html)) — boilerplate CRUD over SQLAlchemy entities. Kato has no SQL persistence today; if it gains one, start here.

The shape of the answer to "should I add X?" is almost always: check if `core-lib` already provides it, and if so, use that.

---

## 3. Top-level package map

```
kato/
  main.py                       — process entrypoint; owns the scan loop + signal handlers
  kato_core_lib.py              — composition root (instantiate-and-inject only; no domain logic)
  config/kato_core_lib.yaml     — Hydra config shell wired to env vars
  client/                       — boundary code: outbound API + subprocess wrappers
    ticket_client_base.py       —   shared protocol for issue platforms
    ticket_client_factory.py    —   chooses the active platform
    youtrack/issues_client.py   —   per-platform issue clients (5 platforms)
    jira/issues_client.py
    github/issues_client.py
    gitlab/issues_client.py
    bitbucket/issues_client.py
    bitbucket/client.py         —   Bitbucket PR + auth (separate from issues)
    pull_request_client_*.py    —   cross-platform PR abstraction
    claude/                     —   Claude Code CLI integration
      cli_client.py             —     one-shot `claude -p`
      streaming_session.py      —     long-lived stream-json subprocess
      session_manager.py        —     per-task session registry + persistence
      session_history.py        —     replays Claude's on-disk JSONL
      wire_protocol.py          —     CLAUDE_EVENT_* + SSE_EVENT_* constants
    openhands/                  —   OpenHands HTTP backend
    openrouter/                 —   OpenRouter helpers (used by openhands)
    retrying_client_base.py     —   HTTP retry wrapper (transient errors only)
  data_layers/
    data/                       —   value types (Task, ReviewComment, fields, tags)
    data_access/                —   raw fetch + parse layer (boundary-aware)
    service/                    —   orchestration; the bulk of kato's logic
      agent_service.py          —     top-level scan-tick handler
      task_preflight_service.py —     resolve repos, prep branches, validations
      task_publisher.py         —     commit, push, PR, review-state move
      task_failure_handler.py   —     failure-comment routing
      review_comment_service.py —     review-comment fix flow
      planning_session_runner.py—     route through streaming Claude
      wait_planning_service.py  —     kato:wait-planning short-circuit
      triage_service.py         —     kato:triage:investigate short-circuit
      workspace_manager.py      —     ~/.kato/workspaces/<task-id>/ folders
      workspace_recovery_service.py — adopt orphan task folders at boot
      parallel_task_runner.py   —     ThreadPoolExecutor wrapper
      repository_inventory_service.py — lazy inventory + tag resolution
      repository_service.py     —     branch state machine + git plumbing (subclass of inventory)
  helpers/                      — cross-cutting *_utils.py modules
    architecture_doc_utils.py   —   reads the file `KATO_ARCHITECTURE_DOC_PATH` points at
    atomic_json_utils.py        —   crash-safe JSON write
    retry_utils.py              —   transient-HTTP retry primitive (used by client/ layer)
    text_utils.py, …            —   small string/dict helpers
  validation/                   — startup + per-task safety checks
  jobs/process_assigned_tasks.py— the cron-scheduled scan job
webserver/
  kato_webserver/app.py         — Flask routes (SSE + POST per-task chat / control)
  kato_webserver/git_diff_utils.py — tree / diff for the right pane
  templates/index.html          — HTML shell
  static/css/app.css            — dark-theme styles
  static/build/app.{js,css}     — Vite-built React bundle (committed)
  ui/                           — Vite + React source for the planning UI
    src/App.jsx, components/, hooks/, constants/, utils/
tests/
  test_*.py                     — flat test layout
  utils.py                      — shared test fixtures
```

---

## 4. Composition + boot sequence

### 4.1 Boot

[`kato.main.main(cfg)`](kato/main.py) drives this sequence:

1. `validate_environment(mode='all')` — reads `.env`, flags missing env vars before anything heavier runs.
2. `KatoInstance.init(cfg)` — instantiates [`KatoCoreLib`](kato/kato_core_lib.py) (a `core_lib.CoreLib` subclass) which wires every service in `_build_agent_service`. **`kato_core_lib.py` is composition-only** — no helpers, no prompt templates, no config-key parsing live there. Domain logic lives in the owning service module; classmethod factories (e.g. `TaskPublisher.max_retries_from_config`) keep config keys next to the feature.
3. `service.validate_connections()` — runs `_validate_git_executable()` (cheap) and skips the per-repo git-access loop unless inventory is already loaded. The repository inventory is **lazy**: `RepositoryInventoryService.__init__` records the config but doesn't walk disk; the walk fires on first `_ensure_repositories()` call (typically the first task's preflight). See §5.1.
4. `_recover_orphan_workspaces(app)` — runs once. [`WorkspaceRecoveryService`](kato/data_layers/service/workspace_recovery_service.py) scans `KATO_WORKSPACES_ROOT` for folders without `.kato-meta.json`, tries to match each to a live task by id + repo tags, and writes the metadata so the orphan becomes a managed workspace.
5. `_start_planning_webserver_if_enabled(app)` — daemon Flask thread on `127.0.0.1:5050` (configurable), shares process memory with the scan loop.
6. `_run_task_scan_loop` — calls `ProcessAssignedTasksJob.run()` (a `core_lib.jobs.Job`) in a sleep-spinner loop (`OPENHANDS_TASK_SCAN_INTERVAL_SECONDS`, default 60s).

### 4.2 One scan tick

`ProcessAssignedTasksJob.run()` does two things:
1. Dispatch any newly-assigned tasks (sequential or parallel, see [`parallel_task_runner.py`](kato/data_layers/service/parallel_task_runner.py)).
2. Poll review tasks for new comments via `service.get_new_pull_request_comments()`. Same call also triggers `_cleanup_done_planning_sessions` which deletes workspace folders for tasks that left the `assigned ∪ review` bucket.

### 4.3 One assigned task

`AgentService.process_assigned_task(task)` walks a fixed pipeline. **Short-circuit handlers run first, in priority order:**

1. **`TriageService`** — task tagged `kato:triage:investigate` → run one read-only Claude turn, parse the response into a `kato:triage:<level>` outcome tag, write it back, remove the investigate tag, return early. See §5.4.
2. **`WaitPlanningService`** — task tagged `kato:wait-planning` → register a planning-UI tab so a human can chat with the agent, return early.

If neither short-circuit fires:

3. **Preflight** ([`task_preflight_service.py`](kato/data_layers/service/task_preflight_service.py)) — model-access check, blocking-comment check (see §5.6), repository resolution (§5.1), workspace clone provisioning, branch prep, push validation. Returns a `PreparedTaskContext` or a skip/failure result.
4. **Move to In Progress** + post `Kato agent started working on this task` comment.
5. **Implementation** — `_run_task_implementation` routes to either `PlanningSessionRunner` (streaming Claude, planning-UI-visible) or `ImplementationService.implement_task` (one-shot).
6. **Testing validation** — optional (gated on `OPENHANDS_SKIP_TESTING`).
7. **Publishing** — `task_publisher.publish_task_execution` creates one PR per repo, treats no-changes repos as clean skips (§5.2), retries transient publish failures up to `KATO_TASK_PUBLISH_MAX_RETRIES` times (§5.3), comments the summary, moves the task to review.

The whole pipeline is read-fresh: kato never relies on in-memory "this task was already processed" state; the ticket system's comments + states are the single source of truth.

---

## 5. Per-feature contracts (the must-know section)

### 5.1 Lazy repository inventory + tag fast-path

[`RepositoryInventoryService`](kato/data_layers/service/repository_inventory_service.py) used to walk every git folder under `REPOSITORY_ROOT_PATH` at startup. On a workstation with dozens of repos, that made `make compose-up` take seconds-to-minutes and demanded credentials for *every* discovered platform. It's now lazy:

- `__init__` materializes only **explicit** `kato.repositories` config (cheap dict→namespace transform). Auto-discovery from `repository_root_path` is deferred.
- `resolve_task_repositories(task)` reads `kato:repo:<name>` tags. For each tag:
  1. **Cache hit?** Return cached.
  2. **Direct folder lookup** at `<REPOSITORY_ROOT_PATH>/<tag>/.git` — if it exists and the folder isn't in the ignore list, build a single inventory entry inline. Avoids the walk entirely for typical workflows.
  3. **Full walk fallback** — only when the fast-path misses. Result is cached per-tag for the rest of the process.
- `_ensure_repositories()` lazy-loads on first read; results are validated for duplicate ids/aliases at load time.

**Ignore-list rules** (`KATO_IGNORED_REPOSITORY_FOLDERS`) — comma-separated folder names. Honored at every layer:
- Auto-discovery walk skips them via `os.walk` dir-pruning ([`repository_discovery_utils.py`](kato/helpers/repository_discovery_utils.py)).
- Fast-path direct lookup checks the ignore set before accepting a candidate.
- A task tag `kato:repo:<name>` whose `<name>` is in the ignore list raises `RepositoryIgnoredByConfigError` and the failure handler posts a clear "Kato refused to run this task" comment — the operator must remove the tag or the ignore-list entry. The contract is about the *name*, not whether the folder happens to exist on disk.

### 5.2 No-changes repos are skipped, not failed

A multi-repo task often tags a repo for *context* (e.g. "look at the client UI to know what shape the backend should expose"); the agent reads it but never edits it. When `task_publisher` reaches that repo's publish step, [`_ensure_branch_is_publishable`](kato/data_layers/service/repository_service.py) raises `RepositoryHasNoChangesError`. The publisher catches it explicitly:

- The repo is moved to a `unchanged_repositories` list (not `failed_repositories`).
- The task still moves to review with PRs only for the repos that actually changed.
- The summary comment ends with `No changes were needed in: <name>` so the reviewer can see the skip was deliberate, not silently forgotten.

This is distinct from a *genuine* publish failure (push rejected, PR API timeout, …) which still routes to `failed_repositories`.

### 5.3 Publish-step retries

`KATO_TASK_PUBLISH_MAX_RETRIES` (default 2 → 3 attempts). Wraps:
- per-repo `create_pull_request` (the push + PR-API call inside `RepositoryPublicationService`)
- `move_task_to_review` (the YouTrack/Jira state transition)

`RepositoryHasNoChangesError` is *not* retried (deterministic, retrying won't fix it). Implementation work — the Claude turn — is **never** re-run on retry; only the publish-side network/API calls are retried with exponential backoff.

When all retries exhaust, the repo lands in `failed_repositories` with the formatted error string, and the YouTrack summary comment includes the reason per repo:

```
Failed repositories:
- ob-love-admin-client: failed to push branch UNA-2574 (remote rejected; tip is behind)
```

### 5.4 Triage short-circuit (`kato:triage:investigate`)

`TaskTags.TRIAGE_INVESTIGATE` flips the orchestrator into classification mode. [`TriageService.handle_task`](kato/data_layers/service/triage_service.py) does:

1. Read task summary + description.
2. Hand them to Claude in *read-only mode* (`ClaudeCliClient.investigate` strips Edit/Write/MultiEdit/Bash/WebFetch from `--allowedTools`).
3. Parse Claude's response for `kato:triage:<level>` where `<level>` ∈ {`critical`, `high`, `medium`, `low`, `duplicate`, `wontfix`, `invalid`, `needs-info`, `blocked`, `question`}. The full set is in [`TRIAGE_OUTCOME_TAGS`](kato/data_layers/data/fields.py).
4. `task_service.add_tag(task.id, <outcome>)` then `remove_tag(task.id, kato:triage:investigate)` so the next scan won't re-trigger.
5. Post a summary comment with Claude's reasoning.

Failure modes are explicit, not silent:
- **Inconclusive** — Claude returned no recognized tag. Comment says so; no tags change.
- **Unavailable** — agent backend has no `investigate` method (OpenHands), or `add_tag` raised `NotImplementedError` (platform without tag support). Comment explains and points the operator at manual action.

### 5.5 Cross-platform tag manipulation

Tag mutation is a contract on `TicketClientBase`. Native overrides exist for the platforms with first-class label/tag APIs:
- **YouTrack** — `/api/issues/<id>/tags` (POST add, DELETE remove by tag id)
- **Jira** — `update.labels[].add|remove`
- **GitHub Issues** — `/issues/<id>/labels` (POST add, DELETE single)
- **GitLab Issues** — `add_labels` / `remove_labels` on the issue PUT

Platforms without native tag mutation (e.g. Bitbucket Issues today) fall through to the **comment-marker fallback** in `TicketClientBase`: kato posts a structured `<!-- kato-tag {"action": "add", "tag": "..."} -->` marker as a comment. Visible in activity logs even when not queryable. Future native overrides supersede the fallback transparently.

### 5.6 Comment-driven blocking + retry override

Kato never keeps an in-memory "this task was already done" flag. Instead, it walks the task's comments and looks for blocking prefixes:

- `Kato completed task ` — terminal (success). Requires explicit override to redo.
- `Kato agent could not safely process this task:` — pre-start failure. Auto-retryable on next scan.
- `Kato agent stopped working on this task:` — execution failure. Requires explicit override.

A subsequent comment whose body starts with `kato: retry approved` (or `kato retry approved`) **clears** the active blocker for that task — kato sees no blocker on the next scan and reprocesses.

Constants live in [`TicketClientBase`](kato/client/ticket_client_base.py): `AGENT_COMPLETION_COMMENT_PREFIX`, `PRE_START_BLOCKING_PREFIXES`, `RETRY_OVERRIDE_COMMAND_PREFIXES`.

### 5.7 Workspace lifecycle

Each task gets its own folder at `KATO_WORKSPACES_ROOT/<task-id>/` (default `~/.kato/workspaces/`). Inside:
- One subfolder per repo the task tags (`<task-id>/<repo-id>/.git/...`).
- A `.kato-meta.json` with `task_id`, `task_summary`, `status` (`provisioning` | `active` | `review` | `done` | `errored` | `terminated`), `repository_ids`, `claude_session_id`, `cwd`, timestamps.

Created in [`provision_task_workspace_clones`](kato/data_layers/service/workspace_manager.py) during preflight. Mirrored to/from kato's session manager so a kato restart can recover the Claude session id even if `~/.kato/sessions/<task-id>.json` was wiped. Deleted by the cleanup loop ([`agent_service._cleanup_done_planning_sessions`](kato/data_layers/service/agent_service.py)) when the ticket leaves both the assigned and the review state — *unless* the workspace status is `active` or `provisioning`, which protects in-flight tasks even when they momentarily disappear from the assigned-tasks query (e.g. between "moved to In Progress" and "moved to Review").

[`WorkspaceRecoveryService`](kato/data_layers/service/workspace_recovery_service.py) handles the inverse case: a workspace folder appears but kato didn't create it (operator dropped a clone in by hand, restored from another machine, etc.). On boot, recovery scans the root, matches each orphan to a live task by id + repo tags, looks up the matching Claude session by walking `~/.claude/projects/*/*.jsonl` for one whose `cwd` resolves to the workspace's repo path, and writes a fresh `.kato-meta.json` so kato adopts it.

**Review-state TTL.** `KATO_WORKSPACE_REVIEW_TTL_SECONDS` (default 3600 = 1 hour) caps how long a `review`-status workspace persists before the cleanup loop deletes it, regardless of whether the ticket is still in the review bucket. Set to 0 to disable. Review-comment processing for tickets whose workspace was already cleaned re-clones on demand. The operator can also force immediate cleanup via the planning UI's "Forget this task" button, which calls `DELETE /api/sessions/<task_id>/workspace`.

### 5.8 Architecture-doc context injection

`KATO_ARCHITECTURE_DOC_PATH` (optional) — file path read on every Claude spawn and appended to Claude's system prompt via `claude --append-system-prompt <text>`. Re-read on each spawn so editing the file takes effect on the next turn without a kato restart. Caps content at 200k chars defensively.

Applies to every spawn: autonomous one-shot ([`ClaudeCliClient`](kato/client/claude/cli_client.py)), planning sessions ([`StreamingClaudeSession`](kato/client/claude/streaming_session.py)), and chat-respawns from idle tabs ([`PlanningSessionRunner.resume_session_for_chat`](kato/data_layers/service/planning_session_runner.py)). Resumed sessions still receive it because Claude rebuilds the system prompt on every spawn — `--resume` only carries the conversation, not the prompt.

### 5.9 Planning UI (Flask + SSE + React)

The webserver ([`webserver/kato_webserver/app.py`](webserver/kato_webserver/app.py)) shares process memory with the scan loop, so `ClaudeSessionManager` and `WorkspaceManager` are the same instances both halves see. Wire-protocol constants are in [`kato/client/claude/wire_protocol.py`](kato/client/claude/wire_protocol.py) (Python) and [`webserver/ui/src/constants/`](webserver/ui/src/constants/) (JS); both sides must agree.

Per-task SSE stream (`/api/sessions/<task-id>/events`) emits:
- **`session_event`** — live event from the Claude subprocess (live state — flips `turnInFlight`, opens permission modal, etc. in the reducer).
- **`session_history_event`** — replayed JSONL from disk when the session is idle/missing. Distinct event type because *historical* events must not flip live-state flags (that was a real bug — a replayed `assistant` event used to leave the working indicator stuck on).
- **`session_idle`** / **`session_missing`** / **`session_closed`** — lifecycle transitions.

Multi-repo task UX:
- **Files tab** ([`FilesTab.jsx`](webserver/ui/src/FilesTab.jsx)) renders one tree per repo (server returns `{ trees: [...] }`).
- **Changes tab** ([`ChangesTab.jsx`](webserver/ui/src/ChangesTab.jsx)) renders per-repo diff accordions with collapse-all / expand-all controls (server returns `{ diffs: [...] }`).
- Both endpoints keep the legacy single-`cwd`/`tree`/`diff` keys populated for back-compat.

Workspace version counter ([`App.jsx`](webserver/ui/src/App.jsx)) bumps per-task on every `result` event so Files + Changes refetch automatically without a manual reload.

**Idle right pane.** When no task is selected, the right pane shows [`OrchestratorActivityFeed`](webserver/ui/src/components/OrchestratorActivityFeed.jsx) — a chronological view of recent scan-loop entries (driven by the same SSE history as the top status bar, with `Idle · next scan in Xs` heartbeats filtered out). Lets the operator watch what kato is doing across all tasks without picking a tab.

**Diff label by workspace status.** [`ChangesTab`](webserver/ui/src/ChangesTab.jsx) reads `workspace_status` from the diff response and labels the header accordingly: `diff` for active, `diff · already pushed (PR open)` for review, `diff · merged` for done, `diff · publish errored` / `diff · terminated` for the failure cases. Prevents "the diff is still showing — did kato break?" confusion when the workspace is just lingering past publish.

**"Forget this task" button.** Per-tab `×` button (visible on hover or when active) calls `DELETE /api/sessions/<task_id>/workspace`, which invokes `WorkspaceManager.delete(task_id)`. Manual escape hatch for tickets where the operator wants the workspace gone immediately rather than waiting for the cleanup loop or the review TTL.

**Auto-focus.** When kato emits an `assistant` event for a task and the operator hasn't manually picked any tab yet, the live task becomes the active tab automatically. A manual click flips a session-long flag that suppresses auto-focus thereafter, so kato never yanks focus mid-investigation. The flag resets on `Forget this task`.

### 5.10 Kato-injected prompt guardrails + bypass-permissions safety gate

Every Claude turn kato spawns includes a security/tool guardrail block ([`cli_client.py`](kato/client/claude/cli_client.py)). These are *advisory* — the model can ignore them. Real safety lives in:
- The `--allowedTools` / `--disallowedTools` flags Claude does enforce.
- `KATO_CLAUDE_BYPASS_PERMISSIONS=false` (default) which routes per-tool permission asks back through the planning UI.
- **Code review on the resulting PR** — that's the actual safety net. See [README.md](README.md#security-model--note).

**Bypass-permissions safety gate.** [`BypassPermissionsValidator`](kato/validation/bypass_permissions_validator.py) runs once at boot, before `KatoCoreLib.__init__`. When `KATO_CLAUDE_BYPASS_PERMISSIONS=true` it: (a) refuses to start when `os.geteuid() == 0`, (b) refuses non-interactive runs (CI/Docker/cron — no TTY for confirmation; there is no flag-only escape hatch), (c) double-prompts the operator with `core_lib.helpers.command_line.prompt_yes_no` on a TTY (the second prompt protects against a fat-fingered Enter on the first), (d) writes an unmissable stderr banner before logger config so it cannot be suppressed by log level. The webserver exposes `/api/safety` returning `{ bypass_permissions, running_as_root }`; the planning UI's [`SafetyBanner`](webserver/ui/src/components/SafetyBanner.jsx) renders a sticky red bar across every page when bypass is on. Per-spawn `WARNING` logs in [`ClaudeCliClient`](kato/client/claude/cli_client.py) and [`StreamingClaudeSession`](kato/client/claude/streaming_session.py) reinforce the state. The configurator ([`configure_project.py`](kato/configure_project.py)) requires typing the literal phrase `I ACCEPT` before writing the flag. Full threat model: [SECURITY.md](SECURITY.md); concrete countermeasures: [BYPASS_PROTECTIONS.md](BYPASS_PROTECTIONS.md).

---

## 6. Conventions

### 6.1 Module placement

- **Service-layer logic lives in `data_layers/service/<name>_service.py`.** When a service starts accumulating a second coherent workflow cluster, split it; don't add more private helpers.
- **`kato_core_lib.py` is composition-only.** Build the dependency graph, inject. No prompt templates, no factory builders, no config-key parsing. If a feature needs a small builder, put it next to the feature (classmethod, module helper, or `helpers/*_utils.py`).
- **Helpers live in `kato/helpers/*_utils.py`.** Shared, stateless, domain-light. Anything stateful belongs in a service.
- **Validation rules live in `kato/validation/`.**
- **Wire-protocol constants are mirrored** between Python (`kato/client/claude/wire_protocol.py`) and JS (`webserver/ui/src/constants/`). Edit both when changing either side.
- **Choose the right `core-lib` layer first.** Outbound API call → `client/`. Raw fetch + parse → `DataAccess`. Multi-boundary orchestration → `Service`. Entrypoint → `Job`. See §2.2.

### 6.2 Tests

- Tests in `tests/` (project-level) and `webserver/tests/` (webserver-only).
- New behavior gets a test. Bug fixes get a regression test. Trust the gut: if you're considering whether a case needs coverage, the answer is yes. See the "Testing" section of [AGENTS.md](AGENTS.md).
- Tests run against the real installed packages; never inject shim modules or fake package facades. **Don't mock `core-lib` itself** — instantiate real `Service` / `DataAccess` subclasses and fake only the external boundary (HTTP, subprocess, filesystem).

### 6.3 Comments

- Default to no comments. Only write one when the *why* is non-obvious (a hidden constraint, a workaround, a contract a future reader would miss).
- Don't explain *what* the code does — well-named identifiers handle that.
- Don't reference the current PR / task / issue number — that rots; PR descriptions don't.

### 6.4 Error reporting

- Per-repo failure reasons must reach the YouTrack/Jira summary comment. The publisher carries `(repo_id, reason)` tuples from `_create_pull_request_for_repository` through to `pull_request_summary_comment` so the operator sees `- backend: <reason>` instead of bare ids. Adding new failure paths? Surface the reason the same way.

---

## 7. How to update this file

When adding a new feature:

1. **Add a §5 subsection** — one paragraph on what the feature does, plus the contract that isn't obvious from reading the code (env vars, blocking conditions, ordering with other handlers, failure modes).
2. **Update the package map (§3)** if a new module landed.
3. **Update the boot sequence (§4.1)** if it touches startup, or the per-task pipeline (§4.3) if it adds a short-circuit handler.
4. **If the feature changes the `core-lib` layering** (new layer, new shared base, new way of wiring), update §2 — that section is the binding contract, not a description of one feature.
5. **Don't copy implementation details** — point to files instead. This doc is a map.

When deleting a feature, leave the section in place with a `**(removed in <yyyy-mm>)**` marker for one release cycle, then prune.
