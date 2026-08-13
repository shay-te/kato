# Kato — Claude Briefing

Kato is an autonomous coding agent. It polls YouTrack/Jira/Bitbucket for assigned tasks, clones repos into isolated per-task workspaces, runs an AI agent (OpenHands or Claude) to implement the fix, then pushes a branch and opens a PR. Also handles Bitbucket PR review comments (fix or answer).

## Run & Test

```bash
pip install -e .     # puts the `kato` CLI on PATH (replaces the Makefile)
kato up              # start kato locally (settings.json + run main)
kato test            # run the unittest suite
```

`kato` is the single operator entry point — `kato up | bootstrap | doctor | test | build-agent-server | sandbox <build|login|verify> | compose-docker`. There is no Makefile. The suite can also be run directly: `python -m unittest discover -s tests -p "test_*.py"`.

**Zero failures AND zero errors expected — everywhere.** `openhands_core_lib` used to carry 94 pre-existing errors that this file told you to ignore; that suite now runs 257 tests clean, so an error there is a real regression, not background noise. Do not reinstate a blanket "ignore" for any lib.

**Keep the code redundancy-free** (full rules in AGENTS.md → "No redundancy"). Before finishing work:
- `cd webserver/ui && npm run dedup` — frontend duplicate-code gate (jscpd; fails above 0.3%; only the 2 intentional clones are allowed).
- `python -m pyflakes kato_core_lib webserver/kato_webserver` — backend dead-import gate (expected hits are the package re-exports in `comment_core_lib/__init__.py`, `data_layers/data/fields.py`, `workspace_manager.py`, plus a couple known unused locals; any NEW finding is dead code to remove).
- Reuse the shared hooks/utils/helpers under `webserver/ui/src/{hooks,utils,stores}` and `kato_core_lib/helpers/*_utils.py` instead of re-implementing; delete orphan (uncalled) code together with its test.

**Never run `npm run build`** — the React bundle is pre-compiled. Running it takes 30+ seconds, requires Node.js to be installed, and is not needed for backend changes or Python tests. To rebuild the frontend (only when changing files under `webserver/ui/src/`):

```bash
cd webserver/ui
npm install
npm run build
```

---

## Core-Lib Architecture

Monorepo of mostly **closed black-box libs**. `kato_core_lib` is the top-level orchestrator. The agent transports (`claude_core_lib`, `codex_core_lib`, `openhands_core_lib`) depend on THREE shared bases, not one:
- **`agent_core_lib`** — the reusable agent-behavior layer (prompt helpers, session/text utils, architecture/lessons readers, the resume-prompt renderer, the generic `workspace_scope_block`). Imports zero other core-libs itself.
- **`sandbox_core_lib`** — workspace-content delimiter framing (`wrap_untrusted_workspace_content`, the prompt-injection defense), the Docker sandbox manager, bypass-permissions validation, system-prompt composition.
- **`provider_client_base`** — the shared `ReviewComment` type and provider-agnostic retry/client-base plumbing.

```
kato_core_lib                  ← orchestrator (imports any lib below; wires PRODUCT-specific
│                                 text — e.g. helpers/workspace_refusal_guidance.py — into
│                                 agent clients via constructor params)
├── agent_backend_core_lib     ← agent-transport CLIENT FACTORY — lazily imports
│                                 claude/codex/openhands_core_lib inside the factory only
├── agent_core_lib             ← SHARED AGENT-BEHAVIOR BASE. Imported by claude/codex/openhands
│                                 + kato + webserver. Generic + product-agnostic: NO kato /
│                                 YouTrack / Jira / Files-tab / UI text. Accepts product text
│                                 only via caller params (e.g. extra_refusal_guidance). Imports
│                                 NO other core-lib, not even lazily (agent_backend_core_lib
│                                 owns the transport factory now, not this lib).
├── sandbox_core_lib           ← Docker sandbox manager, untrusted-content delimiter framing,
│                                 bypass-permissions validation, system-prompt composition
├── claude_core_lib            ← Claude CLI transport   (may import agent_core_lib, sandbox_core_lib,
│                                 provider_client_base)
├── codex_core_lib             ← Codex CLI transport    (same three-lib allowance as above)
├── openhands_core_lib         ← OpenHands transport    (same three-lib allowance as above)
├── git_core_lib               ← GitClientMixin, git subprocess engine, repo discovery utils
├── repository_core_lib        ← provider utils (URL parsing, token messages); imports
│                                 git_core_lib's pure URL-parsing helpers directly (module
│                                 level, not lazy — narrow, stateless, no transport coupling);
│                                 its own pull_request_client_factory lazily imports
│                                 github/gitlab/bitbucket_core_lib inside the factory
├── task_core_lib              ← task data types and platform config; its own
│                                 task_client_factory lazily imports youtrack/jira/
│                                 bitbucket/github/gitlab_core_lib inside the factory
├── bitbucket_core_lib
├── github_core_lib
├── gitlab_core_lib
├── youtrack_core_lib          ← YouTrack API client (fully black-box, see standard below)
├── jira_core_lib
├── workspace_core_lib         ← workspace folder management
└── provider_client_base       ← ReviewComment and shared provider types
```

**Rule:** reusable agent/LLM-behavior code belongs in `agent_core_lib`; the agent transports import it (plus `sandbox_core_lib` for sandbox/prompt-injection concerns and `provider_client_base` for the shared `ReviewComment` type — these three are the full set of sanctioned peer-imports for a transport lib). Provider/transport specifics stay in their own lib. Anything product-specific (ticket workflow, repo publishing, Kato UI, and the *text* of product-specific prompt guidance) stays in `kato_core_lib` and is injected into agent clients as a parameter — `agent_core_lib` must never contain kato-specific workflow/product text. Glue between the non-agent black-box libs still belongs in `kato_core_lib`.

### Core-Lib Quality Standard

Every core-lib must meet all of these (use `youtrack_core_lib` as the reference example):

1. **100% test coverage** — every service function, every permutation of inputs
2. **Flow tests A-Z** — end-to-end flow tests inside the lib's own `tests/` folder (`test_flow.py`)
3. **Minimal peer imports** — only stdlib + third-party packages, with narrow, documented exceptions: any lib may import the shared **`agent_core_lib`** base; the three agent transports (`claude_core_lib`/`codex_core_lib`/`openhands_core_lib`) additionally import **`sandbox_core_lib`** and **`provider_client_base`** (sandbox/prompt-injection concerns and the shared `ReviewComment` type — provider/git/ticket libs still don't need these). `agent_core_lib` itself imports NO other core-lib, not even lazily. The one sanctioned lazy-import pattern is a dedicated **client factory** — `agent_backend_core_lib` (agent transports), `repository_core_lib/client/pull_request_client_factory.py` (github/gitlab/bitbucket), `task_core_lib/client/task_client_factory.py` (youtrack/jira/bitbucket/github/gitlab) — which imports its provider implementations lazily, inside the factory function only. Outside those factories, no lib imports another *transport/provider* lib peer-to-peer, and no git-subprocess call anywhere bypasses `git_core_lib`'s `GitClientMixin`/`build_safe_git_command` (a bare `subprocess.run(['git', ...])` skips the hook-disabling hardening and reopens a real RCE-out-of-sandbox path — this has regressed at least once; see `repository_approval_discovery_service.py`'s `_read_origin_url`, fixed after an audit found it drifted).
4. **No kato references — AT ALL, ENFORCED.** The string `kato` (any case, incl. `KATO_*` env names) must NOT appear ANYWHERE in a core-lib — not source, tests, comments, or field names. All `KATO_*` variables and the `kato` brand live ONLY in `kato_core_lib`; every other lib reads GENERIC names (`AGENT_IGNORED_REPOSITORY_FOLDERS`, `CLAUDE_SESSIONS_ROOT`, …) or takes values via constructor/params, and `kato_core_lib` bridges its `KATO_*` config to those (e.g. `_export_agent_env_from_kato_config()`, `from_config(..., state_dir=...)`, `workspace_refusal_guidance`). This regresses during feature work, so it is gated: **`python -m unittest tests.test_corelib_agnostic_gate`** (runs in `kato test`) — a ratchet that fails the build when a lib gains a kato ref. Fix the code; never raise a ceiling. Full rule: AGENTS.md → "Core-libs stay kato-free".
5. **Tests live inside the lib** — at `<lib>/<lib>/tests/`, not in the top-level `tests/` folder
6. **Check for leaked tests** — after building a lib, grep `kato_core_lib/` and `tests/` for any tests that belong inside the lib instead

```bash
# Check a lib is clean
grep -rn "kato_core_lib" <lib_name>/<lib_name>/ --include="*.py"   # must be empty
grep -rn "<lib_name>" kato_core_lib/ --include="*.py"              # only import lines, no logic leaks
grep -rn "<lib_name>" tests/ --include="*.py"                      # should be empty if tests are inside the lib
```

### What Was Migrated Out of kato_core_lib

Code moved in previous sessions — do not move it back:

| Was in kato_core_lib | Now lives in |
|---|---|
| `helpers/claude_one_shot_utils.py` (re-export) | `claude_core_lib/claude_core_lib/helpers/one_shot_utils.py` |
| `helpers/git_clean_utils.py` (re-export) | `git_core_lib/git_core_lib/helpers/git_clean_utils.py` |
| `helpers/repository_discovery_utils.py` (re-export) | `git_core_lib/git_core_lib/helpers/repository_discovery_utils.py` |
| `data/review_comment.py` (re-export) | `provider_client_base/provider_client_base/data/review_comment.py` (direct import) |
| All git subprocess methods on RepositoryService | `git_core_lib/git_core_lib/client/git_client.py` → `GitClientMixin` |
| `_fallback_web_base_url`, `_provider_from_url_string`, `_default_provider_base_url`, `_missing_pull_request_token_message` | `repository_core_lib/repository_core_lib/helpers/provider_utils.py` |

**RepositoryService inheritance:**
```python
class RepositoryService(GitClientMixin, RepositoryInventoryService): ...
```
`GitClientMixin` owns all `git` subprocess calls. `RepositoryInventoryService` owns repo config/discovery.

---

## Key Files

| File | What it does |
|------|-------------|
| `kato_core_lib/main.py` | Entry point; UI-first boot (serve webserver, then validate + reconcile in a background thread); scan loop (180s interval, no startup delay) |
| `kato_core_lib/jobs/process_assigned_tasks.py` | Each scan cycle: dispatch tasks + review comments |
| `kato_core_lib/data_layers/service/agent_service.py` | Top-level service object — owns all sub-services |
| `kato_core_lib/data_layers/service/task_preflight_service.py` | Pre-flight: resolve repos, clone workspaces, prep branches |
| `kato_core_lib/data_layers/service/workspace_provisioning_service.py` | Parallel git clone per task into `~/.kato/workspaces/<task>/<repo>/` |
| `kato_core_lib/data_layers/service/repository_service.py` | Repo operations (inherits GitClientMixin + RepositoryInventoryService) |
| `kato_core_lib/data_layers/service/repository_inventory_service.py` | Repo config loading + auto-discovery of `.git` folders |
| `kato_core_lib/data_layers/service/task_publisher.py` | Push branch, open PR, move task to "In Review" |
| `kato_core_lib/data_layers/service/review_comment_service.py` | Fix or answer PR review comments |
| `kato_core_lib/validation/startup_dependency_validator.py` | Connections validated in parallel (in the background finalize, not blocking the UI); `{backend}_testing` probe skipped for claude/codex |
| `kato_core_lib/validation/repository_connections.py` | Per-repo git connectivity check |
| `kato_core_lib/helpers/review_comment_utils.py` | `is_question_comment()` heuristic + reply body builders |
| `git_core_lib/git_core_lib/client/git_client.py` | `GitClientMixin` — every `git` subprocess call |
| `git_core_lib/git_core_lib/helpers/repository_discovery_utils.py` | Disk walk to find `.git` folders |
| `repository_core_lib/repository_core_lib/helpers/provider_utils.py` | Provider URL/token utilities |

---

## Flows

### Startup (UI-first)
```
main() → KatoInstance.init(cfg, defer_validation=True)  ← builds the service, does NOT validate yet
       → _load_hooks_or_refuse()                        ← local, fail-closed
       → _start_planning_webserver_if_enabled()         ← the UI is served NOW, in ~seconds
       → background _finalize_configured_boot():        ← off the critical path
             validate_connections()                     ← repos + task + impl (+ testing ONLY for openhands)
             _run_boot_reconciliation()                 ← orphan/branch/status/comment/done-task steps
             _start_post_boot_workers()                 ← incl. warm_up_repository_inventory (disk walk)
             ready_event.set()                          ← releases the scan loop
       → _run_task_scan_loop(ready_event)               ← waits for ready_event; then every 180s
```
Validation no longer blocks the UI. On a configured boot the webserver binds before any
network validation; a validation failure retries + surfaces in the UI banner instead of
exiting. The `{backend}_testing` probe is skipped for the single-binary CLI backends
(claude/codex — same local CLI as the implementation client); only openhands keeps it.

### Task pickup
```
scan → get_assigned_tasks() [API]
     → process_assigned_task(task)
     → prepare_task_execution_context()
         1. resolve_task_repositories()  ← uses cached inventory (warm-up already ran)
         2. provision_workspace_clones() ← parallel git clone (up to 4 at once)
         3. git fetch + checkout branch
     → agent session (OpenHands or Claude streaming)
     → _should_pause_for_push_approval(task)?   ← DEFAULT: yes, always
         YES → _pause_for_push_approval(): stash (task, prepared, execution),
               post a "waiting" ticket comment, workspace → review.
               Nothing is pushed. Publish resumes only via approve_push()
               (the UI's push button).
         NO  → publish_task_execution()
                 → create_pull_request() per repo
                 → if all repos unchanged → status NO_CHANGES, task stays in current state (NOT moved to "In Review")
                 → if PRs created → move to "In Review", post summary comment
```

**Kato never publishes on its own.** Pushing a branch and opening a PR is an
operator action. The pause used to be opt-IN — it fired only for a task
tagged `kato:wait-before-git-push`, so every *untagged* task pushed and opened
a PR autonomously, against the stated policy. `KATO_AUTO_PUSH_ENABLED`
(default `false`, `helpers/push_approval_gate_utils.py`) inverts that: unset,
the flow always parks after testing. The tag still forces a park even with the
switch on — it is the stricter statement. The UI's own Push / Done buttons
(`push_task`, `approve_push`) are operator actions and are never gated.

Pending approvals are in-memory: a kato restart drops them, and `approve_push`
then 404s ("no pending publish for this task"). The branch and commits survive
in the workspace, so the UI's Push button is the recovery path.

### PR review comment
```
scan → get_new_pull_request_comments() on PRs in "In Review"
     → is_question_only_batch()?
         YES → agent answers → post reply with "NO CODE CHANGED" disclaimer → leave thread OPEN
         NO  → agent fixes → _review_fix_produced_changes()?
                  NO  → post "no changes" reply, mark processed, return (thread stays open)
                  YES → push → reply "addressed" (thread left UNRESOLVED — only an
                        operator-triggered resolve via resolve_task_comment mirrors to
                        the remote; the autonomous flow never auto-resolves a thread)
```

**`is_question_comment` heuristic:** requires `?` ending + question start word + no fix keywords + ≤400 chars. Conservative — defaults to fix-mode on ambiguity.

### False-success guards (important — previously bugs)
- Task: all repos unchanged → `NO_CHANGES` status, task NOT moved to "In Review"
- Review answer: thread never auto-resolved, reply always prefixed with visible "no code changed" disclaimer

---

## Config

```yaml
kato:
  task_scan:
    scan_interval_seconds: 180  # default (3 min — a 30s cadence tripped provider rate limits)
```

Auto-discovery: if `REPOSITORY_ROOT_PATH` is set (no explicit `repositories:` list), Kato walks the tree for `.git` folders. Result cached after first run. Background warm-up runs this at boot.

---

## Testing Patterns

- All `unittest.mock.Mock` + `SimpleNamespace` — no network, no DB
- Each core-lib has its own `tests/` folder inside it
- Top-level `tests/` is for kato integration tests only
- Key test files: `test_task_publisher.py`, `test_startup_validator.py`, `test_repository_connections_validator.py`, `test_review_comment_question_mode.py`
