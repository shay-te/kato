# Kato — Claude Briefing

Kato is an autonomous coding agent. It polls YouTrack/Jira/Bitbucket for assigned tasks, clones repos into isolated per-task workspaces, runs an AI agent (OpenHands or Claude) to implement the fix, then pushes a branch and opens a PR. Also handles Bitbucket PR review comments (fix or answer).

## Run & Test

```bash
pip install -e .     # puts the `kato` CLI on PATH (replaces the Makefile)
kato up              # start kato locally (settings.json + run main)
kato test            # run the unittest suite
```

`kato` is the single operator entry point — `kato up | bootstrap | doctor | test | build-agent-server | sandbox <build|login|verify> | compose-docker | lid-awake <setup|remove|status> | history`. There is no Makefile. **ONE list of subcommands: `kato_core_lib/cli.py`.** `./kato` (and `kato.exe`, frozen from `tools/kato/kato.py`) only finds a Python and runs `-m kato_core_lib.cli` (old `sandbox-build` names are aliases): a second list there drifted until `./kato doctor` crashed on a missing module. `cli.py` must stay importable on a bare Python (stdlib only — `./kato bootstrap` runs it before a venv exists; a test runs it with `-I -S`). `kato test` = `scripts/run_all_tests.py`, every test root. `kato.exe` must be rebuilt on Windows after a `tools/kato/kato.py` change. The suite can also be run directly: `python -m unittest discover -s tests -p "test_*.py"`.

**Zero failures AND zero errors expected — everywhere.** `openhands_core_lib` used to carry 94 pre-existing errors that this file told you to ignore; that suite now runs 257 tests clean, so an error there is a real regression, not background noise. Do not reinstate a blanket "ignore" for any lib.

**Keep the code redundancy-free** (full rules in AGENTS.md → "No redundancy"). Before finishing work:
- `cd webserver/ui && npm run dedup` — frontend duplicate-code gate (jscpd; fails above 0.3%; only the 2 intentional clones are allowed).
- `python -m pyflakes kato_core_lib webserver/kato_webserver` — backend dead-import gate (expected hits are the package re-exports in `comment_core_lib/__init__.py`, `data_layers/data/fields.py`, `workspace_manager.py`, plus a couple known unused locals; any NEW finding is dead code to remove).
- Reuse the shared hooks/utils/helpers under `webserver/ui/src/{hooks,utils,stores}` and `kato_core_lib/helpers/*_utils.py` instead of re-implementing; delete orphan (uncalled) code together with its test.

**Agent status has EXACTLY ONE derivation — do not add a second.** Whether an agent is
working / idle / sleeping / closed / waiting / background is decided ONLY by
`webserver/ui/src/utils/agentStatus.js` → `deriveAgentStatus()`, fed by the one store
`webserver/ui/src/stores/agentStatusStore.js`. Every surface (tab dot, tab tooltip, header
dot, backend chips, task palette) renders that function's own `label` / `title` / `dotClass`
— none of them may look at `session.working` or compose their own status sentence.

This has regressed **twice**, and both times the second source hid as a *neighbouring helper*
rather than an obvious copy: `tabStatus.js`'s `deriveTabStatus` answered `WORKING` off the
5s-polled `session.working`, and `tabStatusTitle` hardcoded `"Claude is working"` (also wrong
on a Codex tab). Because `deriveAgentStatus` falls through to `tabStatus`'s value for the dot,
a stale poll painted the dot and tooltip "working" on a finished, merged task while the live
store and the in-chat animation correctly showed nothing running.

Rules: `tabStatus.js` is the WORKSPACE axis ONLY (active/provisioning/review/done/
terminated/errored/idle) and must never mention `working`. Before touching any status surface,
run `grep -rn "AGENT_STATUS_KIND\.\|\.working" webserver/ui/src --include=*.js*` — every hit
outside `agentStatus.js` must be an INPUT passed in, never a decision. If two surfaces could
disagree, fix the derivation, not the surface. The comment-run `kato_status` pill and the
workspace/task status are SEPARATE axes — keep them separate.

**Never run `npm run build`** — the React bundle is pre-compiled. Running it takes 30+ seconds, requires Node.js to be installed, and is not needed for backend changes or Python tests. To rebuild the frontend (only when changing files under `webserver/ui/src/`):

```bash
cd webserver/ui
npm install
npm run build
```

---

## Core-Lib Architecture

Monorepo of mostly **closed black-box libs**. `kato_core_lib` is the top-level orchestrator. The agent transports (`claude_core_lib`, `codex_core_lib`, `grok_core_lib`, `opencode_core_lib`, `openhands_core_lib`) depend on THREE shared bases, not one:
- **`agent_core_lib`** — the reusable agent-behavior layer (prompt helpers, session/text utils, the lessons-document directive + read gate, the resume-prompt renderer, the generic `workspace_scope_block`). Imports zero other core-libs itself.
- **`sandbox_core_lib`** — workspace-content delimiter framing (`wrap_untrusted_workspace_content`, the prompt-injection defense), the Docker sandbox manager, bypass-permissions validation, system-prompt composition.
- **`provider_client_base`** — the shared `ReviewComment` type and provider-agnostic retry/client-base plumbing.

```
kato_core_lib                  ← orchestrator (imports any lib below; wires PRODUCT-specific
│                                 text — e.g. helpers/workspace_refusal_guidance.py — into
│                                 agent clients via constructor params)
├── agent_backend_core_lib     ← agent-transport CLIENT FACTORY — lazily imports
│                                 claude/codex/grok/opencode/openhands_core_lib inside the
│                                 factory only (client, session manager, history, index,
│                                 model catalog — one table row per transport)
├── agent_core_lib             ← SHARED AGENT-BEHAVIOR BASE. Imported by every transport
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
├── grok_core_lib              ← Grok Build CLI transport (xAI `grok`; same allowance)
├── opencode_core_lib          ← OpenCode CLI transport (`opencode`; same allowance)
├── openhands_core_lib         ← OpenHands transport    (same three-lib allowance as above)
├── review_loop_core_lib       ← review loop: an independent read-only reviewer, findings into
│                                 the main chat, repeat until clean. Generic + kato-free: imports
│                                 only stdlib + utils_core_lib; the host implements its ports
│                                 (kato: data_layers/service/review_loop_adapters.py)
├── plan_queue_core_lib        ← plan queue: split an approved plan into small tasks that wait for
│                                 each other (merged AND done) and release them in order. Generic +
│                                 kato-free: stdlib + utils_core_lib; host ports in
│                                 kato_core_lib/data_layers/service/plan_queue/ (one module each)
├── local_task_core_lib        ← tasks created in kato's own UI (no tracker): one JSON store of
│                                 LOCAL-<n> tasks — summary, description, tags, state, comments.
│                                 Kato-free; imports only stdlib + utils_core_lib
├── lid_awake_core_lib         ← keep a computer running with the lid closed and never leave it stuck:
│                                 Mac pmset + root watchdog, Windows powercfg + PowerShell watchdog,
│                                 Linux systemd-inhibit; heartbeat; idle holds. Kato-free; imports only
│                                 stdlib + utils_core_lib; kato's settings key, words and names are in
│                                 kato_core_lib/data_layers/service/lid_awake/
├── git_core_lib               ← GitClientMixin, git subprocess engine, repo discovery utils
├── repository_core_lib        ← provider utils (URL parsing, token messages); imports
│                                 git_core_lib's pure URL-parsing helpers directly (module
│                                 level, not lazy — narrow, stateless, no transport coupling);
│                                 its own pull_request_client_factory lazily imports
│                                 github/gitlab/bitbucket/azure_devops_core_lib inside the factory
├── task_core_lib              ← task data types and platform config; its own
│                                 task_client_factory lazily imports youtrack/jira/
│                                 bitbucket/github/gitlab_core_lib inside the factory
├── bitbucket_core_lib
├── github_core_lib
├── gitlab_core_lib
├── azure_devops_core_lib      ← Azure Repos PR client (git host only — no Azure Boards).
│                                 Owner = "<org>/<project>" (on-prem "<collection>/<project>");
│                                 PAT as Basic; comment id "<thread>-<comment>" (ids repeat per
│                                 thread); "@<GUID>" mentions rewritten to "@{uniqueName}"
├── youtrack_core_lib          ← YouTrack API client (fully black-box, see standard below)
├── jira_core_lib
├── workspace_core_lib         ← workspace folder management
├── provider_client_base       ← ReviewComment and shared provider types
├── utils_core_lib             ← genuinely shared primitives (atomic_write, file_lock,
│                                 text_utils). ANY lib may import it — it is a shared
│                                 base, not a peer transport. It was missing from this
│                                 diagram for a long time, which is how the same helper
│                                 kept getting re-forked with divergent guarantees
├── security_scanner_core_lib  ← pluggable workspace security scanners (bandit,
│                                 detect-secrets, npm audit, env-file)
├── agent_provider_contracts   ← the shared agent-transport interface types
├── vcs_provider_contracts     ← the shared VCS-provider interface types
└── openrouter_core_lib        ← OpenRouter model catalog
```

**The four CLI transports are MIRRORS — keep them one-to-one.** `claude_core_lib`, `codex_core_lib`,
`grok_core_lib` and `opencode_core_lib` have the same files, public names, signatures and class members,
only the agent's name substituted (`CodexCliClient` / `GrokCliClient`, `StreamingCodexSession` / …). The
contract is data — `agent_provider_contracts/cli_transport_layout.py` — and `tests/test_transport_api_parity.py`
enforces it: a module one transport has and the others do not must be DECLARED there as a real CLI difference
(Claude's helpers; OpenCode's `helpers/cli_generation.py`), and the per-turn three must have identical private
client methods. Codex, Grok and OpenCode are all one process per turn, so everything they share lives ONCE in
`agent_core_lib`: `session/per_turn_session.py` (the chat state machine; hooks `_default_command`,
`_translate_event`, `PROMPT_ON_STDIN`, `CLEAN_EXIT_ENDS_TURN`, `_turn_env`), `session/per_turn_manager.py`,
`session/turn_events.py` (THE event vocabulary — Codex's own names; each transport's `session/wire_protocol.py`
translates its CLI into it, so the UI's one renderer draws every per-turn chat), `session/turn_result.py`, and
`per_turn_cli_shared.py` (the spawn-free client half; every `subprocess`/`shutil` call stays in the transport,
where containment tests patch it). Grok and OpenCode ENFORCE strict git per turn (Grok `--deny` rules, OpenCode
permission rules in `OPENCODE_CONFIG_CONTENT`, both from `command_floor.floor_command_prefixes`); neither has a
Docker sandbox image, so Docker mode and bypass are REFUSED for them, never run unsandboxed. Like Codex they are
chat tabs only (`KATO_GROK_ENABLED` / `KATO_OPENCODE_ENABLED`, on by default), never `KATO_AGENT_BACKEND`.
Neither CLI was available to run when they were written: built from their published source, tested against
fake CLIs that parse the real argv — try a real `grok` / `opencode` before trusting a flag change.

**Rule:** reusable agent/LLM-behavior code belongs in `agent_core_lib`; the agent transports import it (plus `sandbox_core_lib` for sandbox/prompt-injection concerns and `provider_client_base` for the shared `ReviewComment` type — these three are the full set of sanctioned peer-imports for a transport lib). Provider/transport specifics stay in their own lib. Anything product-specific (ticket workflow, repo publishing, Kato UI, and the *text* of product-specific prompt guidance) stays in `kato_core_lib` and is injected into agent clients as a parameter — `agent_core_lib` must never contain kato-specific workflow/product text. Glue between the non-agent black-box libs still belongs in `kato_core_lib`.

### Core-Lib Quality Standard

Every core-lib must meet all of these (use `youtrack_core_lib` as the reference example):

1. **100% test coverage** — every service function, every permutation of inputs
2. **Flow tests A-Z** — end-to-end flow tests inside the lib's own `tests/` folder (`test_flow.py`)
3. **Minimal peer imports** — only stdlib + third-party packages, with narrow, documented exceptions: any lib may import the shared **`agent_core_lib`** base; the agent transports (`claude_core_lib`/`codex_core_lib`/`grok_core_lib`/`opencode_core_lib`/`openhands_core_lib`) additionally import **`sandbox_core_lib`** and **`provider_client_base`** (sandbox/prompt-injection concerns and the shared `ReviewComment` type — provider/git/ticket libs still don't need these). `agent_core_lib` itself imports NO other core-lib, not even lazily. The one sanctioned lazy-import pattern is a dedicated **client factory** — `agent_backend_core_lib` (agent transports), `repository_core_lib/client/pull_request_client_factory.py` (github/gitlab/bitbucket/azure devops), `task_core_lib/client/task_client_factory.py` (youtrack/jira/bitbucket/github/gitlab) — which imports its provider implementations lazily, inside the factory function only. Outside those factories, no lib imports another *transport/provider* lib peer-to-peer, and no git-subprocess call anywhere bypasses `git_core_lib`'s `GitClientMixin`/`build_safe_git_command` (a bare `subprocess.run(['git', ...])` skips the hook-disabling hardening and reopens a real RCE-out-of-sandbox path — this has regressed at least once, and an audit had to catch it).
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
| `kato_core_lib/main.py` | Entry point; UI-first boot (serve webserver, then validate + reconcile in a background thread); scan loop (50s interval, no startup delay) |
| `kato_core_lib/jobs/process_assigned_tasks.py` | Each scan cycle: dispatch tasks + review comments |
| `kato_core_lib/data_layers/service/agent_service.py` | Top-level service object — owns all sub-services |
| `kato_core_lib/data_layers/service/task_preflight_service.py` | Pre-flight: resolve repos, clone workspaces, prep branches |
| `kato_core_lib/data_layers/service/workspace_provisioning_service.py` | Parallel git clone per task into `~/.kato/workspaces/<task>/<repo>/` |
| `kato_core_lib/data_layers/service/repository_service.py` | Repo operations (inherits GitClientMixin + RepositoryInventoryService) |
| `kato_core_lib/data_layers/service/repository_inventory_service.py` | Repo config loading + auto-discovery of `.git` folders |
| `kato_core_lib/data_layers/service/task_publisher.py` | Push branch, open PR, move task to "In Review" |
| `kato_core_lib/data_layers/service/review_comment_service.py` | Fix or answer PR review comments |
| `kato_core_lib/validation/startup_dependency_validator.py` | Connections validated in parallel (in the background finalize, not blocking the UI); `{backend}_testing` probe skipped for the CLI backends (claude/codex/grok/opencode) |
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
       → _run_task_scan_loop(ready_event)               ← waits for ready_event; then every 50s
```
Validation no longer blocks the UI. On a configured boot the webserver binds before any
network validation; a validation failure retries + surfaces in the UI banner instead of
exiting. The `{backend}_testing` probe is skipped for the single-binary CLI backends
(claude/codex/grok/opencode — same local CLI as the implementation client); only openhands keeps it.

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
switch on — it is the stricter statement. A `kato:autopilot` ticket is the per-ticket
opt-in the other way: kato presses the operator's own Done once the work (and its tagged review loop) is
finished — see Autopilot below. The UI's own Push / Done buttons
(`push_task`, `approve_push`) are operator actions and are never gated.

Pending approvals are in-memory: a kato restart drops them, and `approve_push`
then 404s ("no pending publish for this task"). The branch and commits survive
in the workspace, so the UI's Push button is the recovery path.

### Shutdown and resume after a restart
```
rail-footer ⏻ Shut down → GET /api/shutdown (who is working) → confirm → POST /api/shutdown (202)
   → 0.5s later signal.raise_signal(SIGINT): THE Ctrl+C handler, not a second path
Ctrl+C / SIGTERM / that button → _cleanup: recorder.record_final() FIRST (working agents → disk,
   recorder frozen) → watchers + service.shutdown() stop every agent → _exit_now
SIGKILL / power cut: nothing runs — the recorder's last write (every 5s, on change) is what is left
next boot → _start_post_boot_workers → _start_restart_resume: read ~/.kato/resume_agents.json
   → AgentResumer: each remembered chat gets "Kato — resumed after a restart … continue" through
     KatoChatChannel (the shared sender, per-task lock, --resume respawn) → then the recorder starts
```
`kato_core_lib/data_layers/service/restart_resume/` (one job per module). "Working" = a live chat mid-turn,
or waiting for the operator's approval inside its turn (`running_agents.py`). The final record must come
BEFORE the agents stop and freeze the recorder: stopping them otherwise records "nothing was running"
(proven out of process: `tests/test_restart_resume_process.py`, real SIGINT, real SIGKILL, real HTTP button).
Remembered agents not resumed yet stay in every write (`pending`), so stopping again during the boot keeps
them. Not resumed: a deleted (forgotten) task, a workspace gone / `done` / `terminated`, a chat with no
`agent_session_id`, one already running again (scan, queued comment). Only WORKING agents come back — an
idle chat stays asleep until written to (lazy resume, unchanged). The route is under the CSRF guard like every
other. **Inside the desktop app the app quits with its kato:** the app starts kato with `KATO_DESKTOP_APP=1`
(native sidecar `.env`, and the WSL `export`), so the shutdown handler also writes `KATO-QUIT` on stderr; the
app's output reader sees it (`says_quit`) and, when the process then ends, calls `exit(0)` instead of showing
"the Kato service stopped". Marker and variable are pinned equal across Python and Rust
(`tests/test_desktop_quits_with_kato.py`). A terminal kato never prints it. The desktop app must be rebuilt.

### Lid-closed running (Settings → "Keep running with the lid closed")
```
tick every 15s (LidAwakeService, lid_awake_core_lib; always started, even with the setting off)
  → inputs: setting, charger, battery %, heat level (NSProcessInfo.thermalState, no root), agents working
  → held awake, or blocks_idle_sleep? the idle hold (below): no root, ends with kato, no help for a closed lid
  → decide():
      off / unsupported / no guard / unknown power / heat ≥ serious / battery ≤ 20% /
      battery + no heat sensor / battery + idle → sleep
      on charger → awake the WHOLE time (a scan has to run for a new ticket to be picked up)
      battery + an agent working (a session waiting for the operator is not working) → awake
  → awake: write the heartbeat FIRST, then the platform's lid switch, read back
  → sleep: only if a heartbeat exists: switch off, read back, delete the heartbeat
```
**A computer that really sleeps runs nothing.** The only way kato "runs during sleep" is that it does not sleep.

| | Lid switch | Idle hold | Crash guard | Heat | Setup |
|---|---|---|---|---|---|
| **Mac** | `sudo -n pmset -a disablesleep 1` | `caffeinate -i -w <pid>` | root LaunchDaemon, every 30s | `NSProcessInfo.thermalState` | sudo, once |
| **Windows** | `powercfg` LIDACTION → 0, AC + DC, old values kept in a file | `SetThreadExecutionState`, own thread | hidden PowerShell started with the hold + a Run value at logon | none: **charger only** | none elevated, once |
| **Linux** | `systemd-inhibit --what=handle-lid-switch:sleep:idle` | same, `sleep:idle` | built in: the inhibited command is the watch loop | `/sys/class/thermal` | none |

Unsupported: WSL (Windows owns the lid; logind in the VM never hears of it), Linux with no logind, anything else.
The rail's lid chip says so and kato changes nothing.

**So the feature is built around a crash.** `pmset disablesleep` is global and root-only: left on, the Mac never
sleeps again, even in a bag. Windows' lid action is the operator's own setting and must come back. A watchdog
restores either when the heartbeat is 2 minutes stale. The file exists only while kato holds the lid, so no file
= hands off (an operator's own `pmset` is never undone); a timestamp, not a pid, because a hung kato still has a
pid. **No guard, no keep-awake:** `decide` refuses unless `guard_ready` confirms it. Mac: daemon loaded AND sudo
allows BOTH directions (setup installs the sudo rule LAST); a LaunchDaemon, not an Agent, because an Agent dies at
logout; the sudoers rule is literal and must match `disable_sleep_command`. Windows: a scheduled task was rejected
because it flashes a console every run. Linux: reading the inhibitor back proves logind has it, NOT that the
desktop obeys it (KDE's PowerDevil handles the lid itself), so a Linux laptop must be tried by hand.

Every platform reports heat on the Mac's 0-3 scale; `HEAT_LEVEL_NO_SENSOR` is not a failed read (`None`): no sensor
is allowed on a charger, never on a battery. The lid hold does not always stop the idle timer (it does not on
Windows), so the service holds both whenever it keeps the computer awake.

Starts before connection validation (`_finalize_configured_boot`, first line): opening the laptop offline must not
switch this off. Off by default. The lid chip's words (rail footer) are `lid_awake/status_text.py`; the page decides nothing.
Do not put a kato name in the lib: pass it in `HostIdentity`. Everything platform-specific is chosen in
`platforms.py`; the service only knows `PlatformSupport`. Windows paths run for real only in CI's
`lid-awake-windows` job (plus `RealWatchdogTests` under any PowerShell).

**A closed lid means no browser, so nothing an agent needs may depend on one.** Remembered permissions used
to be applied only when a browser asked (pending poll, session list, chat stream), and the UI stops polling
while its page is hidden: an agent hit an ask already granted and waited until the page came back ("the review
loop only runs when I focus the tab"). `kato_webserver/permission_auto_resolver.py` runs the same check every 0.25s (it was 2s: an unattended write waited up to 2s, 20-40s a task);
every carve-out lives in `_maybe_auto_resolve_pending`, so do not add one here. Still browser-only: messages
queued in a chat's composer (`useBackgroundQueueDrain`) wait for the UI.

**The run limits changed with this** (`PlanningSessionRunner._wait_for_terminal_event`, runs kato starts itself,
not chats): `KATO_CLAUDE_TIMEOUT_SECONDS` is now an IDLE limit (no event for that long), plus a hard cap
`KATO_CLAUDE_MAX_RUN_SECONDS` (8h). Time waiting for the operator counts towards neither. The fixed limit used to fail
a healthy run at 30 minutes. Both are checked on every poll: the old check ran only when a poll came back empty.

### Lessons — how the agent gets better between tasks
```
ONE document: lessons.md (KATO_LESSONS_PATH, default <KATO_WORKSPACES_ROOT>/lessons.md)

agent spawn → directive names the file; a PreToolUse gate blocks every other tool until it is Read
            → the agent works with it as constraints
            → near the end of the task the AGENT edits it (at most once): records what the task
              taught and — over the size budget (LESSONS_BUDGET_CHARS) — must leave it smaller

operator prompt / diff comment → candidate (lesson-candidates/, never shown to the agent)
     → validated (Done-push, comment addressed, or task deleted with "done") → lessons/<id>.md
     → LessonsService.file_pending(): an AI EDITOR confined to that one file (Read/Grep/Edit only)
       files each lesson — merges it into the rule that covers it, or adds it where its scope is
     → abandoned (task or comment deleted) → discarded
```

**Every write to the lessons document is an AI edit — kato never appends to it.** That is what
makes the file better the more is written to it instead of longer: a rule is FILED (searched
for, merged, deduplicated), never pasted. Do not add a deterministic append, an "inbox"
section, or a path that writes lessons into the file without the editor. Pending lessons wait
in `lessons/` and are deleted only once the editor reports them filed; a run that fails, stops
early, or leaves less than half the document is not taken as filed (and the last is undone).

**There is no second document and no whole-file regeneration — do not add either back.** A
separate "architecture doc" used to be read alongside the lessons file, and a tool-less
"compaction" used to REGENERATE the whole lessons file on every promotion. The two files said
the same kind of thing in two places, and the regeneration only ever grew the file (it was told
to preserve every rule) until it outgrew its own timeout and silently stopped.
`KATO_ARCHITECTURE_DOC_PATH` is read exactly once, at boot, to adopt an old file as the lessons
document (`LessonsService.adopt_legacy_document`); its earlier lessons are queued for the editor.

**Extraction (the task's own lesson, after Done) has its own budget: `_LESSON_EXTRACTION_TIMEOUT_SECONDS` (600s)**,
beside the other one-shot budgets in `kato_core_lib.py` (publish conventions 45s, editor 1800s). It used the shared
120s and timed out on UNA-3416 — a 453-character prompt that takes ~12s idle, run beside the Done push, PR, Slack
and other agents while the Claude CLI retried a busy API itself. Nothing waits on it (background thread). Its context
is cut to `EXTRACTION_CONTEXT_MAX_CHARS` (start + end, `bounded_context`; candidates too). A one-shot that does not
finish is ONE warning line (`LessonsService._ask`; any other exception keeps its traceback); the task's own lesson is
skipped, but candidates promoted for it are already pending files and are filed later — nothing staged is lost.

### Standing instructions — the operator's own text in every task's prompt
Settings → Prompts → Standing instructions (`KATO_STANDING_INSTRUCTIONS`, a `textarea` schema field
of General lifted with `panel: 'prompts'`, ≤ `STANDING_INSTRUCTIONS_MAX_CHARS` — the system prompt is
argv, and Windows caps a command line at 32,767). `helpers/standing_instructions_utils.py`
`with_standing_instructions(guidance)` appends it after kato's own `extra_system_prompt`, so it lands
in `--append-system-prompt` AFTER the boundary + lessons directive. Read fresh (`live_setting`) at the
Claude spawn site, `PlanningSessionRunner.start_session`, so a save needs no restart (a running chat keeps
its prompt). Not on Codex chats or one-shot runs (neither carries `extra_system_prompt`).

### Release notes — a dated line per task that reaches Done
```
"Finish & forget" + done ─┐
plan board "Count as done" ┴─▶ TaskStateService.move_task_to_done ─▶ _announce_done ─▶ ReleaseNotesService.task_done
ticket closed in YouTrack/Jira ─▶ cleanup sweep ─▶ ReleaseNotesDoneWatcher (asks the tracker: really Done?) ─┘
task_done: 1. NOW (the forget flow deletes the workspace next): read the task (ReleaseNoteSources), write
              "- <date> — <title> (<ID>) <!-- kato:task ID -->" under ## Unreleased → ### <repo>  [locked, atomic]
           2. background: ReleaseNoteWriter (one-shot model line) + ReleaseNoteLinks (PRs by branch) replace
              that line — only if it is still unchanged (an operator edit wins)
           → ReleaseNotesGoogleSync (destination file_and_google_doc only): replace a Google Doc with the file
```
- **ONE place announces done:** `move_task_to_done` = `_move_to_done` (subclasses override HOW, e.g. LOCAL tasks)
  + `_announce_done`. A failed move raises and announces nothing; a failing observer never fails the done. Do not
  add a second announcement elsewhere — the Done/Push button is NOT done (it moves to In Review). Its one
  observer is `DoneAnnouncements` (`done_announcements.py`), which tells each on its own: the release notes,
  **the Slack post**, and the task's containers (stopped) — add a new "on done" there, never a second hook.
  The ONE exception is the Slack post, which the Done button also makes (below): the team's "done" is the
  button, and the post is kept to one per task either way.
- **Slack on done** (`slack_done_announcer.py` + `slack_core_lib`, kato-free). ONE post per task PER COMMITTED
  STATE (`SlackPostedTasks`, `~/.kato/slack_posted_tasks.json`), from whichever comes first: the header's **Done**
  button (`finish_task_planning_session` → `publish.announce_finished` = `announcer.announce`, run NOW so the
  answer's `slack` {status: posted | posted_again | already_posted | not_set_up | failed, error} reaches the Done
  toast: "✓ posted to Slack", or "⚠ not posted to Slack: <why>" listed first and amber) — or the tracker's Done
  (`DoneAnnouncements` → `task_done`, the task read NOW, posted in the background, skipped if Done already
  posted it). The operator thinks "Done" means the button: wiring Slack only to `move_task_to_done` meant the
  button never posted ("I marked the task done, no Slack"). Where it goes (Settings → Email & Slack, read fresh):
  `KATO_SLACK_DONE_CHANNEL` holds an **Incoming Webhook URL** (`hooks.slack.com/services/…`, needs nothing
  else — `SlackWebhook`; the URL is the credential, so the field is `secret` and an error never repeats it)
  or a channel link + `KATO_SLACK_BOT_TOKEN` (xoxb-, `chat:write`, app invited — `SlackClient`). A half set-up
  says what is missing. The post is SIGNED — "Finished by *Shay Tessler's kato*" (`helpers/agent_name_utils.py`): several
  developers' katos post to one channel. `KATO_AGENT_NAME` (Settings → Email & Slack, live), else git's global
  `user.name` (an email there → the account's full name, else its local part: never post an address), else the host.
  Its PRs (`ReleaseNoteLinks`, by branch) are the release notes' links, each shown as its
  WHOLE link (`• backend: <url>`; a lone PR just `• <url>`) — a word like "pull request" hid where it went. Everything from a
  ticket is escaped (`<!channel>` in a title must never notify a channel). A failed post is not marked posted:
  the next Done tries again. **Posting is CLAIMED, not checked** (`SlackDoneAnnouncer._claim`, one lock): Done on a
  plan sub-task calls `move_task_to_done` (→ `task_done`, background) and THEN `announce` (toast); both used to check
  "posted?" before either marked it, and the channel got the task twice. A second caller while a post is under way
  waits for it (`WAIT_FOR_POST_SECONDS`) and reports its outcome; a failed post gives the claim back.
  **Posted AGAIN once something was committed since** ("I clicked Done, it said already posted", after the agent
  kept working): each post keeps the commit every repository of the task was on (`TaskCommits` →
  `GitClientMixin.last_commit`), and a Done (or the tracker's Done) whose repositories moved on posts again, marked
  "_Updated: it changed since it was last posted here._" → `posted_again`. A commit, not a file on disk: Done commits
  the edits before it pushes, and an uncommitted edit never reached the team. An entry from before commits were kept
  is a bare epoch: a commit newer than it counts. Commits that cannot be read (no workspace, no clone, an empty
  branch) are "already posted" with the reason in `error` — never a second post on a guess. A merge of the default
  branch is a commit too, so it counts as a change.
- **The task's Docker containers are stopped on done** (`task_containers.py` + `container_core_lib`, kato-free).
  An agent runs `docker compose up` / `docker run` like a developer and nothing stopped them: a finished task
  kept its database and dev server running. A container belongs to a task when its Compose project (working dir
  or compose file) is in the task folder, or it bind-mounts anything from it — no mark of kato's needed. Kato's
  sandbox is left out (`SANDBOX_CONTAINER_LABEL`; it removes its own). Paths compare across Docker Desktop's
  forms (`/run/desktop/mnt/host/c/…`, `/host_mnt/…`, WSL `/mnt/c/…`, a Windows path without case) and through a
  link (the folder as given AND its realpath). Done = STOPPED (reversible, off the done path). Deleting the task
  (the forget route, step 1b, after the session is terminated) = REMOVED with their anonymous volumes and — for a
  Compose project with no container left anywhere — its networks and volumes; the answer's `containers` is
  toasted. The header shows them (`TaskContainersChip`, title area, only when there are any; re-read on the
  agent's status change, never polled) with Stop all / Remove all. NEVER on a turn ending or the agent stopping:
  a dev server the operator still uses must not vanish. Proven on real Docker (compose stack + `docker run`).
- **Tickets closed outside kato** are noticed as "left the live list" (NOT the workspace flip to `done` — in-review
  workspaces are always protected, so merged-then-closed never flips) and CONFIRMED with `get_issues_status`
  (YouTrack/Jira only; other trackers: only kato-marked done adds entries). First sweep only records; ≤5 per
  sweep; "could not ask" retried 3 sweeps. State: `~/.kato/release_notes_watch.json`.
- **The marker `<!-- kato:task ID -->` is the idempotency key** — anywhere in the file, including an entry the
  operator moved into a version section. Entry text is untrusted (`clean_line`): it can never forge a heading,
  marker or second line; the date is kato's, never the model's.
- **Settings → Release notes** (all read fresh from settings.json — no restart; `_live_settings_keys`):
  enabled (default on), path (default `~/.kato/release-notes.md`, refused inside `KATO_WORKSPACES_ROOT` — it would
  be committed with a task), destination (`file` | `file_and_google_doc`), Google Doc link, service-account key.
- **Google Doc copy** = `google_docs_core_lib` (kato-free; `GoogleDocsClient`/`ServiceAccountTokenClient` extend
  core-lib's `ClientBase`; service-account JWT signed with `cryptography`, no google-auth dependency): Drive
  `files.update` media upload of HTML into the existing Doc. The local file is the master; the Doc is overwritten.
  Opt-in (titles leave the machine). Failures are kept in `~/.kato/release_notes_sync.json` and shown in the
  Release notes dialog (rail footer) with "Sync now". Never touch `desktop/RELEASE_NOTES_<v>.md` (the app's own notes).

### Strict git (Settings → its own "Strict git" tab, `KATO_AGENT_GIT_STRICT`, default ON, GLOBAL: Claude + Codex)
```
ON  (strict): the agent owns the INDEX + WORKING TREE; kato owns refs, commits, remotes, history, config
OFF (local):  the agent may also run commit / merge / rebase / reset / cherry-pick / revert / am / tag
              on the branch that is checked out. Still kato's: push/pull/fetch/clone/remote, leaving the
              branch (checkout/switch/branch/bisect/symbolic-ref), config/worktree/submodule, plumbing
```
**ONE list per level: `agent_core_lib/helpers/command_floor.py`** (`git_denied_subcommands`, `GIT_LOCAL_HISTORY_SUBCOMMANDS`,
`git_access_prompt_text`). Both enforcement layers read it — Claude's `--disallowedTools`
(`ClaudeCliClient._merge_disallowed_with_floor(git_access=)`) and the Action Guard's `git.orchestrator_owned`
(`classify_action(git_access=)`, which used to keep its own drifted set). Never add a second list.
- Read fresh at the one spawn funnel (`PlanningSessionRunner.start_session` → `agent_git_access()`); the session decides
  its level ONCE (`StreamingClaudeSession.git_access`, `effective_git_access`: **Auto/bypassPermissions is always strict**)
  and the deny list + the system prompt's git block both read it. `_tool_floor_change_needs_respawn` respawns a chat whose
  floor no longer matches, so flipping the switch reaches a running chat on its next message. One-shot runs
  (`claude -p`, the reviewer) stay strict.
- **Codex** has no deny flag and no system prompt; its own `workspace-write` sandbox is the wall, and it makes every
  root's `.git` READ-ONLY (strict: even `git add` fails, "Unable to create .git/index.lock"). `StreamingCodexSession`
  asks `git_access_source` (kato: `agent_git_access`, wired in `kato_core_lib.py`) at the START OF EVERY TURN; at local
  it adds `-c sandbox_workspace_write.writable_roots=[<root>/.git …]` — `-c`, NOT `--add-dir`: proven on codex-cli
  0.166, a resumed turn does not keep the first turn's `--add-dir` (and rejects the flag), while `-c` works on both.
  The rules ride in front of the message on stdin, only when the level changed (`git_access_change_text`, incl.
  "STRICT again"); the transcript keeps what the operator typed. Leaving the branch / pushing are prompt-only for
  Codex — there is no per-command block. The same `-c` carries the task's OTHER repositories on every turn
  (`_writable_roots`): a resumed turn dropped `--add-dir` too, so from its second message a Codex chat could no
  longer write to them. Proven on the real CLI with the repositories OUTSIDE every temp folder — Codex lets anything
  write to `$TMPDIR`, so a repro there passes with or without the fix.
- Push stays kato's in BOTH modes: `KATO_AUTO_PUSH_ENABLED`, "kato never publishes on its own", the plan queue's
  merge checks and the remote check before every push all depend on it. Do not add an "agent may push" level.
- Kato's publish path already handled agent commits: a clean tree skips kato's commit and pushes what is ahead;
  the diff/review loop read from the merge-base; a review fix counts a moved HEAD.
- What strict-off needed (and has): `_push_branch` refuses a branch whose PUSHED commits were rewritten
  (`rewritten_pushed_commit`: the tracking ref is no longer contained AND was once the branch tip in its reflog —
  a fetched commit never was, so "the remote moved" still gets the old fetch + rebase); `git_operation_in_progress`
  (rebase/merge/am/cherry-pick/revert/bisect) stops self-heal, branch prep and publish from touching a half-done
  rebase; an `index.lock` younger than `GIT_INDEX_LOCK_LIVE_SECONDS` is never deleted (the process check only
  recognises kato's own `-C` git); the git routes (push, pull, merge, update source, PR, approve, Done) answer 409
  while a LOCAL session is mid-turn (`kato_webserver/agent_git_busy.py`); `git reset --hard` asks
  (`fs.git_reset_hard`) and is remembered apart from `git reset`.
- The field sits in General with `panel: 'strict-git'` → its own tab (`StrictGitSettingsPanel`: what it is, why it is
  recommended, what off changes, what never happens). The UI asks before the switch goes OFF: the schema field's
  `confirm` → `SettingConfirmDialog` (portalled to `<body>`, Escape closes only the popup). Any field can carry one.

### Automation (Settings → its own "Automation" tab): what kato does on its own in a task
One switch per behaviour, grouped by when a task meets it (`AutomationSettingsPanel`): security scan → run tests →
auto push, PR conventions, stop containers on Done, release notes, emails → answering ticket / PR comments. The
switches are schema fields tagged `panel: 'automation'` (lifted out of General, Security scanner, Release notes,
Email); `SchemaFieldGroup keys=[…]` puts one section's switches under several headings. Every "on by default"
switch is in `DEFAULT_ON_BOOL_KEYS` (release notes and the security scan were drawn OFF while kato ran them).
Read-fresh switches are in `_live_settings_keys` (`_read_fresh_switch_keys`): Strict git, auto push and PR
conventions used to answer "restart required" though they never needed one.
- **`KATO_RUN_TESTS`** (`automation_switches_utils`, default ON, read fresh). Off: (1) kato's testing step is skipped
  at its one gate, `AgentService._run_task_testing_validation` — the old `OPENHANDS_SKIP_TESTING` (boot-read,
  mislabelled: it always applied to every backend) still works beside it; (2) every chat kato starts gets
  `run_tests_utils.NO_TESTS_GUIDANCE` in its system prompt (`with_test_policy` at the `start_session` funnel, before
  the standing instructions) — it says it overrides later instructions to run tests, because the review loop's fixer
  rules ("run the tests that cover it") arrive as a chat message and its `findings_guidance` is fixed at boot;
  (3) autopilot's section drops "and its tests pass" (`AUTOPILOT_GUIDANCE_NO_TESTS`); (4) every review loop start
  forces `verify_tests` off (`review_loop_routes._with_test_policy`). A running chat keeps the prompt it started
  with; a RESUMED loop keeps the stages it was started with. Codex chats get no system prompt, so (2) is Claude-only.
- **`KATO_STOP_CONTAINERS_ON_DONE`** (default ON): `TaskContainers.task_done` returns early when off.
- **Not offered, on purpose:** a switch for kato's own ticket comments — the shared plan's record IS a ticket comment
  other katos read (`plan_record_on_tracker`), and several flows depend on theirs; one gate at `add_comment` would
  break them. Moving ticket states automatically — a task left Open is picked up again on the next scan.

### The tasks rail (left edge, Chrome-style vertical tabs) and the task switcher
There is no task strip at the top and **no app bar** any more. Every task is a ROW in a rail on the left
(`TabList`, `#tabs-pane` `.tabs-pane-side`), `position: fixed`, top to bottom of the screen, z-index 900, ALWAYS
there, COLLAPSED to each row's dot + task code, bare (no row box except the task on screen, one waiting for you, a
pinned one), `$TASK-RAIL-WIDTH` 118px — the one column the page gives up (`#root`'s left padding), as narrow as a
WHOLE code allows: codes are never cut or stripped of their prefix ("…" costs as much as "UNA"). Hovering the rail expands it OVER the page after `TASK_MENU_OPEN_DELAY_MS` (a pointer
only passing the edge does nothing; pointing at the kato logo expands at once) and it PUSHES NOTHING; leaving
collapses it after `TASK_MENU_CLOSE_GRACE_MS` (`stores/taskMenuStore.js`); Escape and a press outside collapse
it. **Expanding moves nothing already on screen** (proven in Chromium: every row's top/height/dot, the logo, the
footer's icons): rows state their height (the collapsed rail hides their 16px controls), the footer's icons are
three to a row and the waiting chips wrap within `$TASK-RAIL-INNER` in BOTH widths, and the actions the collapsed
rail cannot fit keep their place `visibility: hidden`. Never `overflow: hidden` on the rail: the footer's
`data-tooltip` labels are CSS-drawn beside their controls (`tooltip-above tooltip-anchor-left`, the rail being the
screen's bottom-left). The app bar's contents moved INTO the rail: `MenuHandle` (the kato logo + menu bars +
orange count of tasks waiting, and "Kato") heads it; `MenuFooter` is pinned to its foot (`margin-top: auto`,
grows upward so nothing pushes the rows): the waiting-task chips (`#menu-attention-slot`, portaled by
`GlobalPermissionContainer`), the status line (click → activity feed), the lid chip, then usage, release notes,
settings, shut down. **Scan now (the rail's actions) is the ONE refresh:** it scans, reloads the list, and re-probes
the agent CLI version + model catalogues (`refreshAgentVersion`, `refreshCatalogs`). A second footer "Refresh" did
only the last two plus a list reload (the list polls itself) under the same icon, claimed to "re-scan tickets", and
was removed on request — do not add it back. With no task it is held expanded (first run). Each row: status dot (the one
derivation), id + name (capitalized for display only; the label is a flex row so the name is its own box — inline
after the id the browser read "B-2admin" and skipped the first word), then the task's review loop and split-plan
MARKS — done or not, nothing to click (`ReviewLoopTabBadge`: turning indigo while running, green clean, the
outcome's colour otherwise, kept once seen; `PlanQueueTabBadge`: amber/orange/grey while live, green once its
newest plan FINISHED — `plan_queue_finished` on the tab list ← `PlanQueueService.finished_sources`; no fetch per
row) — then pencil, pin, ×. **The review loop and split-plan BUTTONS are in the task toolbar** (`ReviewLoopButton`,
`PlanQueueButton`, their own group before the prompts): they moved into the rows for a day and the operator
asked for them back ("on the tab I want to see only indication it was done or not"). No scroll chevrons, no
sub-task second row — removed with their code.
**A CLICK on the kato logo PINS the rail open** (`toggleTaskMenuPinned`, `stores/taskMenuStore.js`; hover still opens
it over the page as before): pinned it ignores the pointer, Escape and a press outside, and it is DOCKED — the page
gives up its width (`#root` padding = `--task-rail-docked-width`, set by `TabList`'s `useDockedRailWidth` while pinned)
— and its right edge (`TaskMenuResizer`) drags its width (240–640px, double-click = 360, arrow keys ±16). The width is
also how wide the hover menu opens (`--task-rail-open-width`). Pin + width remembered per browser
(`kato.taskMenu.pinned.v1` / `.width.v1`). A second click unpins and collapses. The nav's class is `is-docked`, NOT
`is-pinned` (that is a pinned ROW).
**Rows are in start order, the latest-started LAST** — decided ONLY by `_in_start_order` (`webserver/kato_webserver/app.py`,
the record's `created_at_epoch`, ties by id); the UI keeps it after pins and dragged rows. It was the folders' names (task-number order).
**A group folds:** a main task's row carries a toggle (chevron + its sub-task count, always shown). Folded
(remembered per browser, `kato.tabs.collapsedGroups`, `readCollapsedGroups` in `pinnedTabs.js`) hides its
sub-tasks EXCEPT the one on screen, so the menu always shows where you are; a hidden one waiting for you turns
the toggle orange (the header badge's colour and source). Tab / Ctrl+Tab still walk folded sub-tasks.
**Task switcher (Cmd+Tab-style):** Tab / Shift+Tab and Ctrl+Tab / Ctrl+Shift+Tab move between tasks in the menu's
order (`useTaskTabShortcuts`; Ctrl+Tab works even in a text field; in a plain browser the browser keeps Ctrl+Tab for
itself, the desktop app does not). Each switch `announceTaskSwitch()`es; `TaskSwitcher` shows the tasks centred,
the landed one highlighted, and fades `TASK_SWITCHER_HOLD_MS` after the last press. Proven in headless Chromium
(Playwright) against a real kato webserver: the rail collapsed to codes, expanded over the page with nothing
moving, rows grouped, footer labels on screen, the switcher came and went.

### Commit messages and PR text can follow a repository's own CLAUDE.md / AGENTS.md
Kato's backend — never the agent — writes the commit and opens the pull request (see "Git:" in Plan
queue below: the orchestration layer owns BRANCH STATE and PUBLISHING only). So a developer's own
commit/PR conventions, written in their repo's `CLAUDE.md`, never reached that text — the agent reads
them (Claude Code loads `CLAUDE.md` natively), but the agent never runs git. `KATO_PUBLISH_FOLLOWS_PROJECT_CONVENTIONS`
(off by default — this changes text in a shared repo's history and PRs) turns on a small EXTRA one-shot
model call, same shape as `ReleaseNoteWriter`: it reads the repositories' own `CLAUDE.md` (else `AGENTS.md`
— `agent_core_lib/helpers/project_conventions_utils.py`, a plain file read, CLAUDE.md preferred per
directory) and asks `PublishConventionsWriter` (`kato_core_lib/data_layers/service/publish_conventions_writer.py`)
to reshape kato's own commit message / PR title / PR description to match — plain markers
(`COMMIT MESSAGE:` / `PR TITLE:` / `PR DESCRIPTION:`), each section falling back independently, never all-or-nothing.
One real difference from the release-note writer: a release note is improved AFTER publish, in the
background, because nothing is waiting on it; this runs SYNCHRONOUSLY before the commit, on a short
dedicated timeout (45s, not the 120s default). No conventions file anywhere, the setting off, or the
one-shot call failing for any reason: kato's own plain text, byte for byte — the writer's `write()`
never raises, so no call site needs a second guard. Three call sites share the one writer (injected via
`kato_core_lib.py`'s `_build_publish_conventions_writer`, same `_claude_one_shot_settings` as release
notes): `task_publisher.py`'s autonomous publish (title + description + commit message, once per task,
not per repo — matching how `pull_request_title`/`_task_commit_message` already worked), `task_publish_service.py`'s
on-demand push (`push_task`, commit message only) and on-demand PR creation (`create_pull_request_for_task`,
all three), and `review_comment_service.py`'s review-fix commit (commit message only — it replies on an
already-open PR, no new title/description). **Deliberately untouched:** the agent's own `pr_description.md`
override (`repository_service.py`'s `_pr_description_from_task_folder`, resolved per repo, deep inside
`_publish_branch_updates`) still wins over this either way — this only replaces what `pull_request_description(task,
execution)` computes as the structured DEFAULT, used when the agent wrote no override.

### Review loop (from the review loop view, or a `kato:review-loop` ticket tag)
**The tag:** `kato:review-loop` (default rounds) or `kato:review-loop:<n>` on a task kato runs by itself starts
the loop once the task parks (`AgentService._start_review_loop_if_tagged`, the last step of
`_pause_for_push_approval`). No tag, no loop, and no setting: it spends tokens, so it is asked for per ticket.
A loop that cannot start is logged and the task stays parked. Only tasks that park get it: wait-planning and
other chats return before the park (a chat has no single finished moment), and auto-push publishes instead.
The agent service reaches the webserver's diffs through `parked_task_listener`, set twice on purpose: when the
routes register, and in `main._mark_webserver_configured`, because in setup mode the service does not exist yet.
```
start (POST /api/sessions/<id>/review-loop {max_rounds: 1..30, default 5}) → ReviewLoopService
  (review_loop_core_lib) thread per task
  round n: wait for the chat to be free → fresh READ-ONLY reviewer: the WHOLE diff first, then
           only what MOVED since the last review; a sweep reads the whole diff again
           (ImplementationService.investigate: no --resume, no session persistence, Bash/Edit denied)
     no BLOCKER/MAJOR → CLEAN │ every blocking issue left survived 2 fixes, none new → STUCK │
     otherwise → findings (ids R<n>-<k> + invariants) posted into the MAIN chat (MainChatDelivery —
            the comment runs' own sender and per-task lock) → wait for that fix turn to end →
            parse its <review-response> into the DECISION LEDGER (new issues always go to the
            chat; a fix that missed once is resent marked ``repeat_of`` + ``missed_fixes`` —
            "you said R2-1 was fixed; still here")
     n == max → MAX_ROUNDS (that fix is never re-reviewed — see below) │ otherwise → round n+1,
            whose reviewer is told the ledger
```
**A review is held to the task, not to everything that could be better.** A fresh reader always
finds something, and each fix is more code for the next one to find it in: on 18 saved loops at least 25
of the 50 blockers after round 1 were in files no fix had touched, UNA-3348 grew from 7 files to 17, and
UNA-3242 (a page move) was blocked by bugs the moved page had always had. Four things keep a long loop on
course. None is optional, and none is a setting:
- **The task includes its plan.** `review_loop_routes._task_text` appends `plan.md` to the ticket (UNA-3343's
  whole ticket was a title; its plan said what was not needed). So the plan is now a yardstick: one that fell
  behind the chat reads as a `task` finding, which the fixer rejects with evidence (that settles it).
- **Every finding says how it belongs** (`relevance`: `task` / `regression` / `unrelated`,
  `ReviewFinding.is_blocking`). The loop enforces it: `unrelated` is counted apart (`OFF_TASK`), shown, never
  sent, never blocks. Left blank or misspelled, the finding's severity decides as it did before: an odd word
  proves nothing about a bug. `NEVER_BLOCKS` in `reviewer_prompt.py` is the bar, shared with the self-check.
- **A re-check reads only what moved** (`diff.render_changes_since`, a diff of the two diffs: ~6.5% of the
  reading on the saved loops). A round compares with the last FINISHED review: `round-N-files.json` holds a
  digest per file (over-budget files included), `round-N-diff.json` stays the whole change, and what the
  reviewer got is `round-N-changes.json`. Nothing moved, a repository that cannot be read, or an older loop →
  the whole change. The first review and every sweep read everything, so all code is still read at its
  final version; a clean re-check says it covered only what moved.
- **A sweep is told what was ruled out** (rejected / out_of_scope WITH evidence), never how the fixes went.
  Those rulings are about the task, not the fixes. Told nothing, it reopened them in new words the fingerprint
  could not match (UNA-3348 round 5: 7 raised, 3 caught). `blind` stays true for it.

**A FAILED loop says why in the agent's own words.** A failed chat turn's reason is `ChatTurnEnd.error`
(`main_chat_delivery.turn_failure`: the result's `result`, else `errors`, else the stderr kato attached, else what
its subtype means — `error_max_turns` → "the agent used up its maximum number of turns"); it used to be only "the
chat's test run turn ended with an error". A chat that never freed up names what it was busy with; an exception
names its type. A round stays `sent` until its fix turn ends (`fixed_at`), so a finding with no `fixed_at` reads "In
progress" while running and "Not fixed — Resume continues" after — never "Not answered" (that needs a reply).

**An empty change is `NOTHING_TO_REVIEW`, neither CLEAN nor FAILED.** Checked first, before a self-check or a
test run spends a turn on it. CLEAN is a verdict on code someone read, so a green result on nothing would be a false
pass; FAILED means something broke. A plan's parent task is the usual case: its work is in its sub-tasks. A repository
whose diff could not be READ also has zero files, and stays FAILED with the repo and the error: never "no changes".

**A blocking finding on the last round is still sent — never discarded for want of a round to
re-check it.** The old rule ("n == max → MAX_ROUNDS, nothing sent") spent a whole review finding
a real blocker and then threw the finding away, unfixed, because no round n+1 was left to confirm
a fix: tokens spent finding it, nothing done about it. `_after_blocking_review` now always sends
(`_send_findings` + `_await_fix`) before checking the round number; only AFTER that fix turn ends
does `_after_fix_for_blocking_round` end the loop as MAX_ROUNDS, when it was the last round — one
round, never two, it just no longer skips the send. The same applies to a STUCK round that happens
to land on the last round (Resume still offers it "one more try", same as any other round —
`ReviewLoopState.resume_point`'s `SEND_FINDINGS` branch no longer refuses past `max_rounds`). A
finding's own status line says so once fixed with no decision recorded ("this was the last round,
so nothing reviews it again") rather than promising a next review that will never run. Untouched:
a last round whose TESTS keep failing after a CLEAN verdict still goes unsent
(`_send_failing_tests`'s own `review_follows` guard) — there the round has no blocking finding to
act on, only a fix no review would ever see either way, so there is nothing to gain by sending it.

**Optional stages** (lib default OFF; in kato `verify_tests`, `confirm_clean`, `extra_sweep` start
ON and `self_check` OFF — checkboxes in the loop view, remembered in `kato.reviewLoopStages.v2`;
route `POST .../review-loop {self_check, verify_tests, confirm_clean, extra_sweep}`: an explicit
bool is the pick, anything else the default. The defaults live twice — `REVIEW_LOOP_STAGE_DEFAULTS`
(JS) and `review_loop_routes._STAGE_DEFAULTS` — pinned equal by a test):
- `self_check` — before round 1, the MAIN chat reviews + fixes its own change (≤3 turns, ends on
  its `<self-check>{"clean": true}` block). Cheap: it already holds the context.
- `verify_tests` — NO REVIEW STARTS ON CODE WHOSE TESTS FAIL. Every round runs the tests in
  the main chat (`<test-report>`) BEFORE its review, unless they already passed on exactly this
  code (each run records the digest it was asked to test); failures are sent back ("fix the
  failing tests"), fixed and run again in the round until they pass, then it reviews. (Tests
  used to run only after a CLEAN review, so while reviews kept finding issues the loop went
  round after round on failing tests.) Steps are read off the round
  (`ReviewRound.tests_step(digest)`: `test_runs` + `tests_fixes`), so Resume takes the same one.
  `RunnerOptions.tests_fix_attempts` (3) fixes that don't land → STUCK (Resume = one more try).
  A test that cannot even START in the task folder for a reason no change to the task's code can fix (a package or
  sibling library not installed there, a service not running) goes in the report's `cannot_run`, NOT `failures`:
  shown (amber, "could not run here"), never sent to be fixed, never blocks the review. UNA-3406 spent all 3 fix
  turns on "No module named 'cloudinary'" and ended STUCK. An import error of a module the task added or changed is
  still a failure (the prompt says so), and the chat is told not to install or fake the missing piece.
  The last round fixes its tests too — its review comes after. Only if the code moves on
  during a review are they run again after it: a fix there closes the round `tests_fixed`
  (the next round reviews it); on the last round nothing is sent → MAX_ROUNDS.
- `confirm_clean` — a clean verdict from a reviewer told the ledger gets a clean-room SWEEP
  (fresh reviewer, ledger withheld; `round.sweep`) that must agree.
- `extra_sweep` — ANY clean verdict gets that sweep ("it was clean, we ran it again, boom a
  MAJOR": one clean review is not proof). A sweep that finds something sends it back.
Messages carry `Kato review loop — {stage}` headers (pinned to `REVIEW_LOOP_STAGE_PATTERN`).

**The reviewer's model** is the operator's pick in the loop view ("Reviewed by", remembered in
`kato.reviewLoopModel.v1`; route `{model}`, validated as a model id) → `ReviewLoopState.model` →
`Reviewer.review(model=)` → `investigate(model=)` → the one-shot's `--model` / `-m`. '' = the
backend client's configured model (`KATO_CLAUDE_MODEL`), which `GET /api/review-loop/default-model`
names so the picker shows it concretely (never "Default"); picking it stores ''. The task's chat
model picker never affects the reviewer — the fix turns run in the chat, on the chat's model.

**A clean verdict covers the exact code reviewed — nothing else.** Each round records
`diff_digest` (`diff.candidate_digest`: sha256 of every repo's diff); before CLEAN is accepted the
diff is read again, and if it moved (operator chatted during a long review, a comment run, the
tests turn touched a file) the round closes `changed` and the same kind of review is redone
(a sweep as a sweep; last round → MAX_ROUNDS, never CLEAN). "Tests passed" is bound the same
way: re-run only when a clean review saw a different digest than the tests passed on.

**Decision ledger:** the fixer answers each finding fixed (+ regression test) / rejected /
out_of_scope (+ evidence; for out_of_scope, what the task asks and why this is not part of it). A rejection WITH evidence settles the finding: a later reviewer
re-raising it without `new_evidence` gets it marked settled (shown, not sent, not blocking).
A bare disagreement settles nothing; a missing response block = "unanswered", never a failure.
Modelled on reviewed-in-rounds PRs where the implementer rejects a wrong finding with proof.
The loop never commits or pushes; `<KATO_TASK_DONE>` is ignored while it runs
(`AgentService.finish_from_done_marker`). Rounds live in `~/.kato/review_loops/` (outside
every clone). The chat's Stop, a task delete, and kato shutdown end it; a restart marks
it interrupted (never resumed on its own). A fix paused on a permission ask shows "waiting for your
approval" and is never treated as stalled. Each review run (a fresh one-shot `claude -p`)
gets its OWN timeout — `KATO_REVIEW_LOOP_TIMEOUT_SECONDS` (default 7200, clamp ≥60), passed
per-call to `investigate(timeout_seconds=)` — NOT the transport's 1800s implementation default,
which a big multi-repo task's review (slower on Windows) overruns, failing the whole loop. UI:
`webserver/ui/src/components/reviewLoop/` —
a loop button in the task toolbar (opens the view; nothing starts from it; a dot while a loop runs or
finished unseen; it also raises the "finished" toast) + a done-or-not mark on the task's row in the tasks
menu + "where is it now" chip beside the header title,
centre-pane view with the round picker (remembered in `kato.reviewLoopRounds.v1`),
transcript label.

**Resume** (the view's Resume button → `POST /api/sessions/<id>/review-loop/resume`): a
STOPPED / FAILED / INTERRUPTED / STUCK loop picks up — the SAME loop (loop_id, rounds, ledger,
stages, model) — at the step it was cut off in, which `ReviewLoopState.resume_point()` reads
from the saved rounds (so loops saved before Resume existed resume too): an unfinished review
is run again IN THE SAME ROUND (the reviewer is a fresh one-shot — there is no session to
`--resume`); a chat cut off mid-fix gets a `Kato review loop — continue round N` nudge, never
the findings twice, and its reply is still read for the `<review-response>` ledger; findings or
failing tests never sent (or held back as STUCK) are sent; a cut-off self-check / test run is
asked again. The summary's `resume` names the step ('' = no button). CLEAN is done outright; MAX_ROUNDS
is done too, but only after its round's blocking findings (if any) were already sent and given their one
fix — see the false-success-guards note above. The loop's between-rounds memory (missed
fixes, first ids, sweep-next, tests digest) is DERIVED from the saved rounds at every round
(`progress.loop_memory`) — never carried in locals — so a resumed loop judges each review
exactly as an uninterrupted one would. Do not reintroduce loop-local memory.

### Autopilot (`kato:autopilot` ticket tag): kato presses Done when the work is finished
```
any read of the ticket (pickup's handle_task, the started-task sweep) → WaitPlanningService.observe_autopilot
   → helpers/autopilot_store.py (~/.kato/autopilot_tasks.json, KATO_AUTOPILOT_PATH); the tag off = released
spawn: plan_mode_store.unpicked_task_mode → Auto (bypassPermissions) unless the operator picked a mode
       (the composer's _task_mode_of asks the SAME function, so the picker shows Auto);
       helpers/autopilot_guidance_utils.py → "print <KATO_TASK_DONE> when done" in the system prompt
"the work is finished" → data_layers/service/autopilot_finish.py AutopilotFinisher:
   chat: the done token (finish_from_done_marker) — kato's own run in flight? wait for its park
   kato's own run: the park after tests (_pause_for_push_approval, after the tag's review loop starts)
   → kato:review-loop tag? the loop first; its end (build_review_loop_service on_finished) → CLEAN only
   → publish.finish_task_planning_session(task, by_operator=True)   ← THE Done button's call
```
**Done is the operator's own Done — nothing of its own.** Push, PR, the ticket's move (a plan sub-task to Done),
Slack: all from `finish_task_planning_session(by_operator=True)`. Do not add a second publish path for autopilot.
Not pressed (said in the task's status feed, the task left to the operator): a `kato:wait-before-git-push` tag
(the stricter statement), a task that cannot be read (its loop/push tags are unknown), a loop ending any way but
CLEAN (stuck, max rounds, stopped, failed, nothing to review), the done token during a running loop (ignored as
before). A finished Done now also drops the task's parked "Approve push" entry (it was just published). Anyone
who can tag the ticket can turn this on: it skips the operator's look before the push (the PR is still reviewed).

**`kato:auto-wait`: the same road, and it stops before Done.** Auto mode, the work, then the review loop — the default
rounds unless the ticket's own `kato:review-loop:<n>` names them (`AgentService._review_loop_rounds`) — and then the
task waits for the operator's own Done: never a push, a PR or a ticket move of its own, whatever the loop says
(`AutopilotFinisher` asks the level; only `autopilot` presses). Its done token never publishes (in a chat it starts the
loop), and `KATO_AUTO_PUSH_ENABLED` does not skip its park (`_should_pause_for_push_approval`). With both tags auto-wait
wins — not pressing Done is the stricter statement. ONE store holds the level: `helpers/autopilot_store.py`
(`automation_level` → `'autopilot' | 'auto-wait' | ''`, `~/.kato/autopilot_tasks.json` as `{task: level}`; an older file —
a plain list — still reads as autopilot), set from the tags by `kato_tag_utils.automation_level_from_tags`; Auto mode and
the agent's "finish by yourself" section ask `runs_by_itself` / the level. The tab list carries `automation`; an
auto-wait row in the tasks rail has a green border (`.tabs-pane-side .tab.is-auto-wait`, kept collapsed too).

### Hand over a task to another kato user (the header's "Hand over")
```
header ⇄ (lifecycle group, before Stop; hidden unless GET /api/sessions/<t>/handover → available)
  → picker: GET …/handover/users (the tracker's assignable people) → pick one
  → UnpushedWorkWarning purpose="handover" (their kato clones from the remote) → POST …/handover {handle, name}
  → TaskHandover.hand_over: 409 while the agent is mid-turn (running_agents.is_mid_turn)
     1. assign_verified (tracker_assignment.py: assign + READ BACK)   ← fails → nothing else runs
     2. tag kato:wait-editing                                          ← fails → stops: never released untagged
     3. clear_pickup_block(delete_own=True) (pickup_block.py): delete the BLOCKING comments THIS account wrote
     4. comment "kato: retry approved — handed over to <name> by <agent_name()>…" (an override every kato honours)
     5. TaskMoves → Open (HANDED_OVER)  ← LAST ticket write: their kato sees it only once all of the above is on it
     6. stop_task_agent (task_agent_stop.py — the chat's Stop, one helper for both); the workspace is kept
```
Each step is reported (`formatHandoverResult`: problems red, first). **"Also delete it from my tasks"** (the picker's
checkbox, unticked every time it opens) runs the tab's own delete (App's `doForgetTask` → `DELETE …/workspace`, no
second dialog, never `?done=1`) only once the result's `landed` is true — assign, tag AND Open all done
(`task_handover.LANDING_STEPS`); otherwise the task is kept and the toast says so. Not offered for a task a plan THIS
kato drives was split from (`kato_webserver/driven_plan.py`, the ONE answer the delete's sub-task cascade also uses):
the delete would take the plan's sub-tasks with it. Asked again each time the picker opens; no answer = not offered. ONE home each, gated by
`tests/test_task_handover.py` `SingleOwnerGateTests`: `assign_issue` only in `tracker_assignment.py` (the plan
queue's release uses it too — it now reads the assignee back), `delete_comment` only in `pickup_block.py` (the plan
queue's "released again" note uses it with `delete_own=False`), `list_assignable_users` only in `task_handover.py`.
A person's comment is never deleted (author must be the token's `current_user()` AND worded as blocking); a comment
worded like kato's but written by someone else is cleared by step 4's override instead. Trackers: `supports_handover`
(on top of `supports_subtasks`) — YouTrack (Assignee field's user bundle, else `/api/users`; `DELETE` comment) and Jira
(`user/assignable/search`, people only; `DELETE` comment). GitHub / GitLab / Bitbucket / no tracker / a LOCAL task:
the button is hidden (`unavailable_reason`). Kato cannot know who runs a kato: anyone assignable is listed. Written
from the trackers' docs; not yet run against a live YouTrack/Jira (the bundle read may need project-admin read access —
it falls back to every user).

### Plan queue (split a plan into small dependent tasks)
```
"Split plan" (the task toolbar / plan pane) → the task's plan TAB in the centre strip
  → POST /api/sessions/<task>/plan-queue
  → snapshot <workspace>/plan.md into the plan (DONE wipes the workspace) → PROPOSING
    — or {split_folder}: a split the operator already WROTE as files (split_folders.py: a folder
      directly in the task folder, ≥2 numbered *.md, README = overview) → one step per file, its
      text kept EXACTLY as the brief; the proposer only names steps, depends_on and repositories
  → ProposalWorker asks IN THE TASK'S CHAT (`plan_queue/split_in_chat.py`: the review loop's
    `KatoChatChannel` — same sender + per-task lock, minus its Plan-mode refusal), pointing at
    plan.md / the split files instead of pasting them; the agent answers there with a
    <task-split> block → validate_split (the ONE validator — proposer output, chat replies AND
    manual edits) → PROPOSED (one auto-repair retry; then PROPOSE_FAILED)
  → the operator STEERS IT IN THE CHAT ("merge those two", "we already have X"): every tick, a
    newer chat reply carrying a <task-split> replaces the proposal (`chat_seen_at`); also the
    tab's edit / remove / revise → Approve (+ Run)
  → PlanTicker every 5s (stateless; reads everything from the saved plan):
      track each released step's task → once its task is DONE, poll its PRs (branch = task id,
      state=all, into the MAIN task's branch or the repo destination, CREATED AFTER the release)
      → MERGED → satisfied
      → a step whose every parent is satisfied → ready → released (local: create LOCAL-n
        "UNA-1234 · Task n: …" IN_PROGRESS + start the chat in the plan's start mode)
```
**Each task plans first by default.** Approve carries "Plan each task first" (ticked): `start_mode=plan` —
a local step's chat opens in Plan mode; a shared sub-task gets `kato:wait-planning` AT CREATION
(`TrackerStepLauncher.prepare`; a failed tag fails the create, never "runs unplanned"), so every kato's
pickup opens it in Plan and it codes once its plan is approved in its chat. Unticked = `implement`.
**A started board card's Done only moves its ticket to Done** (operator, 2026-11-05: "just move the task to done,
don't try to create PRs"): `PlanBoard.finishStep` → `POST …/steps/<id>/done` → `AgentService.mark_task_done` =
`move_task_to_done` (every "on done" observer hears it). No push, no pull request — the card is often ANOTHER
kato's task (assigned to a teammate), with no clone here. Refused for a step not handed over yet (`prepared_only`:
that is "Count as done"'s job). It is NOT "Count as done": the tasks after it still wait for its pull request to be
merged. **The task's own Done (header) is still the publishing one** (`finish_task_planning_session(task,
by_operator=True)`): it pushes,
open its PR into the main task's branch, and — for a plan's sub-task (`_is_plan_subtask`: it has a
`plan_merge_branch`) — move its ticket to the tracker's **Done** (not In Review) through `move_task_to_done`, so
every "on done" observer hears it: release notes, Slack, containers, and `PlanQueueDoneNudge`, which makes the
board read that task again at once (`PlanQueueService.task_changed` → the tracker reader's `forget`; it used to
wait out the minute's cache). The card then shows "Waiting for merge" and is Done once its PR is merged — the
tasks after it never start without its code. Only the operator closes a ticket: the agent's `<KATO_TASK_DONE>`
calls the same function WITHOUT `by_operator` and still moves to In Review. A sub-task left in In Review used to
sit there forever ("I click Done on a subtask, the board never moves").
**Deleting a sub-task** (the card's trash, any state, plans this kato drives): `POST …/steps/<id>/delete` →
`plan_queue/step_deletion.py` declines every OPEN pull request of its branch in each of its repositories
(`RepositoryService.decline_open_pull_requests` → `PullRequestDataAccess` → `repository_core_lib` →
`decline_pull_request` on the host: Bitbucket decline, GitHub/GitLab close, Azure DevOps abandon — the
`PullRequestProvider` contract), and only when every decline worked takes the step out of the plan
(`PlanQueueService.remove_step`: the tasks waiting on it stop waiting; a tracker ticket is withdrawn — noted,
unassigned, never deleted; a plan keeps one step). A decline that failed answers 502 and changes nothing more.
The board then deletes the task itself with the tab's ✕ call (`forgetTaskWorkspace`: folder, chat, containers).
**Deleting the task a plan was split from cascades the other way.** `forget_task_workspace` (any task, not just a
plan's source) calls `_forget_cascaded_sub_tasks` FIRST: a plan this kato drives (never a FOLLOWED one — no
authority to touch it) has every sub-task's pull requests declined (`PlanStepDeletion.decline_pull_requests`,
now public, reused rather than re-derived) and its own workspace/session/containers forgotten through the exact
same `_forget_one_task_result` the parent itself goes through next (the route's old body, extracted so a
cascade can call it per sub-task). The whole plan is then `stop()`ped once (if it still has steps in flight) —
never `remove_step` per sub-task: there is no "rest of the plan" to keep healthy once its source is gone, and
`remove_step` refuses to take out the last one anyway. A sub-task's declined-PR failure is reported in its own
`sub_tasks` entry and never blocks the parent's own delete, same precedent `containers` already sets in this
function — an un-declined PR outliving the clone it pointed at is better than an orphaned workspace left behind
forever. `?done=1` on the parent is never propagated to its sub-tasks' own tickets. `ForgetTaskModal` warns
about the sub-task count before the operator confirms (`session.plan_queue.step_count`, already on the tab
list); `doForgetTask`'s success toast names what happened to each cascaded sub-task
(`forgottenSubTasksToast`, `planStepDeleteText.js`) — never silent on one that did not fully clean up.
**Update source on a sub-task puts the whole plan on disk** (`TaskPublishService.update_source_for_task`): a repo
the sub-task CHANGED goes to its own branch (as for any task); every other repo — one of the sub-task's it did not
change, and one only the MAIN task works in (`_main_task_repository_ids`: the main task's workspace record, so only
when this kato has it) — goes to the main task's branch (`plan_merge_branch`), where the plan's merged work is, but
only when the remote has that branch (`remote_has_branch`; else skipped with why). Reported apart as
`main_branch_repositories` (toast: "✓ on UNA-1, the main task's branch, …"). They used to be skipped ("no changes in
workspace clone"), leaving the operator's source on master, so the sub-task could not be tested with the plan.

**Update source of ONE repository** (the Changes tab's per-repo header button, `RepoUpdateSourceButton`; route body
`{repository_id}` / `{repository_ids}` → `update_source_for_task(task, repository_ids=…)`, which narrows the push too):
only that repository is pushed and its source switched — every other source stays put (an operator testing a client
change while Docker built from the backend checkout). Same task git slot (`gitActionKey(task, 'update-source')`) and
toast as the header's button. A sub-task's main-task-only repository asked for alone goes to the main branch; an id
the task does not have fails ("not one of this task's repositories") and moves nothing.

**The main task follows its merged sub-tasks:** when the ticker sees a step's pull request merged
(`merged_listener`), `plan_queue/main_task_follows.py` runs the main task's Pull (`publish.pull_task`,
fast-forward only; a dirty or diverged clone is left alone and logged) in the background — its clones were
never pulled, so its Changes said "Nothing changed yet" while its branch held the whole plan's work.
**Run starts every ready step; a card's Start starts ONE** (`start_step` → `PlanTicker.release_now`, any live
plan, even approved/paused; only the driver; only a READY step). **"Done" is the provider's done state OR one
listed in `KATO_DONE_STATES`** (`helpers/done_states_utils.py`, General settings, read fresh). The plan on
UNA-3267 stalled for good because its first tasks sat in "Waiting Deploy": an unknown state read as "still
to do", and YouTrack does not mark it resolved, so its own flag cannot say. The board shows each ticket's
own state (`Step.task_state`) and says "handed over — not started yet" for a STARTING step (it sat in "In
progress" looking like work while kato had not picked it up, e.g. `KATO_MAX_PARALLEL_TASKS` full).
It stalled AGAIN on UNA-3370 because that kato never listed the state — so **the board now says it**: a
started task whose ticket state the workflow does not name (`TaskStateService.known_state_names()` + the
listed ones → `unrecognized_states`, sent as the board's `unknown_states`, tracker plans only) gets
"“Waiting Deploy” is not counted as done here" and a one-click "Count “Waiting Deploy” as done"
(`POST /api/plan-queues/done-states` → `add_done_state`, written to settings.json, live). Do NOT offer a
plain "Count as done" on a started card instead: its dependents would start before its pull request is
merged into the main branch, without its work — the merge check is what keeps them honest.
**An approved plan's waiting tasks can be edited** ("Edit tasks" on the board, the same `PUT .../steps`, the
proposal's rows from `PlanStepRows.jsx`): `live_edit.py` — only a step still BLOCKED/READY and never handed
over (`can_change`; a step someone took early has `released_at`) changes or is taken out; a started one
must come back exactly as it is, else 409 and nothing changes. On a shared tracker: taken out → withdrawn
(ticket kept, unassigned, noted), new → `prepare` (parents first), changed text/footer → `launcher.update`
(`update_subtask`, YouTrack/Jira) AFTER the new tickets exist; a rewrite that fails keeps the edit and
answers 503 naming the ticket (save again retries).
**A dependent step starts only when every parent is merged AND its task is done**, and then
clones fresh from the MAIN task's branch, where its parents merged — no stacked branches, no
cloning from another workspace. **Branch prep fetches the plan's branch BEFORE choosing the base**
(`_prepare_task_branch` → `_fetch_plan_merge_branch`, then `destination_branch`): it used to choose
first, so a sub-task clone made before the main branch reached the remote (or before its parents merged
into it) was cut from master and its agent never saw that work ("misses those changes … starts to
hallucinate"). Every sub-task's log line says which branch it starts from (`task X: <repo> starts from Y`).
**A sub-task merges into the main task's branch** (the task the plan was split from, by its
`task_branch_name`), never the repository destination: the main task carries the whole plan there
in one PR. ONE answer, `RepositoryService.destination_branch(clone)`: for a clone of a sub-task
(`_task_id_of_clone`: the workspace folder above it) it is that branch ONCE THE CLONE SEES
`origin/<branch>`; before that, the repo destination (where it will be cut from). Every reader of a
destination gets it from there: branch prep (which no longer reads the clone's attribute first: it
is copied from the inventory's configured destination), PR creation, the empty-PR and publishable
checks, the header's "merge the default branch" (`_merge_preflight` fetches the main branch FIRST:
a clone made before a sibling's PR created it still merges it, never master; the publish state's
`merge_branch` names it in the button's tooltip), and the Changes tab / review loop
(`configured_destination_branch(repo, local_path=<clone>)` → `destination_branch_of_clone`). Which task is a sub-task:
`plan_queue/merge_target.py` — the queue (`source_of_step_task`, ANY plan state, so plans split
before this exist are covered), then what `LocalStepLauncher` announced (a local step clones before
the queue records its task), then a tracker sub-task's footer. **Nothing is pushed before the
operator's push:** `create_pull_request` first calls `put_plan_merge_branch_on_remote`, which cuts a
missing main branch from the repo's OWN destination (`_own_destination_branch`, the inventory's
answer — never the clone's, which may name a stale or deleted plan branch) as the remote holds it,
with an empty lease. The probes accept a PR into either branch (older plans merged into the
destination); the history probe reads the main branch first. A live plan's source task cannot be
renamed (its sub-tasks' PRs point at its branch). Open PRs created before this are NOT retargeted.
**The main task's Pull merges; every other task's Pull only fast-forwards.** Sub-task PRs land on the main
task's REMOTE branch while its clone gains commits of its own (a "Merge master"), so the two sides split and
`pull --ff-only` refused for good (UNA-3267). For a task a plan was split from (`AgentService._is_plan_source`:
`plan_for_source`, ANY plan state — a finished plan leaves the branch just as split), `pull_task` passes
`merge_when_diverged` and `pull_workspace_clone` merges `origin/<branch>` in through `_merge_into_clone`, the ONE
merge Merge master also uses. Never a rebase: it would copy master's commits onto the branch as new ones and
need a force push onto the branch sub-tasks merge into. Nothing is pushed. Pressed by the operator, a conflict is
left for the agent (told which files, like Merge master); the plan's own follow-up pull after a merge
(`MainTaskFollowsMerges`, `keep_conflicts=False`) undoes it instead. Any other task reports `diverged`.
**Where a sub-task works: its own list, starting with all of the main task's.** The split's agent
is NOT asked (`PlanQueueService(proposer_picks_repositories=False)`, kato's `plan_queue/wiring.py`):
every step starts in all of the plan's `repository_ids`, and the operator ticks/unticks per task
— and adds one kato knows (the `+` on a task's row, which also adds it to the plan) — in the proposal
and in Edit tasks (only tasks not started). That list is the truth: a local
step is created with it (its `kato:repo:` tags), a tracker sub-task carries it in its footer
(rewritten by `launcher.update` when a waiting task is edited). A repository added to the MAIN task
later ("+ Add repository" → `TaskRepositoryService._share_with_plans` → `PlanQueueService.add_repositories`)
goes to the plan and every step NOT started (rewriting waiting tickets); a started one keeps what its
agent was given. (Briefly this was "every sub-task always works in all of them, live"; the operator
reversed it to per-task editing — do not bring back resolving a sub-task from its main task's
workspace.)
**Where a task works and what it runs on are ON ITS ROW, never behind the open** (`StepSetup` in
`PlanStepRows.jsx`: repository chips, `+`, a model picker; "it's hard to find" when they sat inside the
opened editor). Above the list, "Every task" (`PlanTaskSettings.jsx`) puts a repository in or out of every
task, adds one, and picks the model + effort they all run on — in the proposal AND in Edit tasks, where
every change to "every task" skips the started ones (`useStepDrafts(…, mayChange)`). A step's own `model`
('' = the plan's `step_model`) wins in `build_request`; picking the plan's model again stores '' so the
task follows the plan. Both pickers show the concrete model, never "Default"; an edit sends `model` /
`effort` only when the operator picked them (Approve sends the pick, else what the plan holds), so the
configured default is never pinned by accident. A tracker sub-task carries its model as a `kato:model`
tag (`TrackerStepLauncher._run_on_model`, set on create and rewrite, so every kato's pickup reads it); a
model no tag can say (a Codex model name) is REFUSED before the ticket is created or rewritten, never
dropped. Effort does not reach tracker tickets.
Kato still never publishes: the step parks for push like any task; merging and moving it to
done are the operator's. The merge verdict ignores PRs created before the step's release
(`CLOCK_SKEW_SECONDS`) — branch names repeat (LOCAL ids restart after a store reset; the same
plan split twice), and an old merged PR must never release a dependent early. "Could not ask"
(`ProbeError`, from `RepositoryService.pull_request_states`, which raises) is never "no PR";
probes back off 15→300s, ≤4 per tick, "Check now" resets. No PR at all after DONE → "no pull
request found — Count as done" (a no-changes step). No queue cap: kato's own limits apply.
**The board's "Sync" button** (`PlanQueueService.sync_now`, any live plan — a follower's board
offers it too, unlike "Take over"/"Edit tasks") drops the step-progress cache
(`TrackerTaskProgress.forget`, 60s) and the driver-record cache (`TrackerRecordSource.forget`,
also 60s — its own docstring already said "the operator asked to look now", years before a
button called it) for every step/record this plan has, then calls the service's own public
`tick()` — the SAME pass the background thread runs every few seconds, just now. No second
tracking/merge-check path exists to drift from that one. A plan not yet approved
(`has_steps_in_flight` False) is left alone.
Every change is compare-and-set through `PlanRegistry.mutate` (saved only on change). Pause /
Stop stop RELEASES only — a released task is an ordinary task. Restart: PROPOSING →
PROPOSE_FAILED, RUNNING → PAUSED (press Run), never resumed on its own. Plans live in
`~/.kato/plan_queues/` (`KATO_PLAN_QUEUES_DIR`); the split one-shot's timeout is
`KATO_PLAN_SPLIT_TIMEOUT_SECONDS` (1800, ≥60 — waiting for the chat to be free, then its answer). Built only when local tasks are (`plan_queue`
is `None` otherwise). UI: `components/planQueue/` — a TAB among the task's file tabs
(`planQueueTab` in `utils/fileTabs.js`: a virtual tab, `view: plan_queue`, path
`plan-queue:<task>`; `PlanQueueView` is its pane) — NEVER a popup: a proposal takes minutes and a
modal blocked the whole window. It shows the split folders found + "split plan.md", the
proposal, then the board (Waiting / In progress / In review / Waiting for merge / Done; cards never
move by hand: a ready card offers Start, a waiting one Count as done (confirmed — it moves the ticket to
done and frees what waits on it), a stuck one Retry / Count as done; "Edit tasks" for the waiting ones; a
card's code opens its task). Polled only while the tab shows; `planQueueChanges` refreshes its summary at once. Its states are the QUEUE's
axis (`planQueueHelpers.js`) — never `session.working`, never `AGENT_STATUS_KIND` (a node test
greps the folder). The task a plan was split from shows it on its ROW's plan mark: live
(`session.plan_queue` on the 5s tab list ← `PlanQueueService.live_by_source`) or finished
(`plan_queue_finished` ← `finished_sources`), no timer of its own; a sub-task's row shows `Subtask of UNA-1:` as a fixed mark when it is NOT grouped under its main
task, never as its name, and a rename edits only the name (`subtaskLabel.js`, pinned to
`step_footer.subtask_summary` by a test).
**Sub-task rows are grouped under their main task** in the tasks menu (`utils/subtaskTabs.js`
`groupSubtaskTabs`, used by `TabList`): indented under it, each with `deriveAgentStatus`'s own dot. A pinned
one keeps its place among the pins; one whose main task has no row here is a row of its own; a loop of
parents groups nothing. Tab / Ctrl+Tab walk the menu's order (each task, then its sub-tasks). The parent comes from `plan_parent` on
the tab list (`PlanQueueService.step_task_sources`, any plan state, previous task ids too — local steps
are titled `UNA-1 · Task n:` and have no "Subtask of" to read), else the ticket's "Subtask of X:" title.

**A live plan's tasks are known by CODE, never by assignee.** The queue holds a step UNASSIGNED until its
turn and hands a task back by unassigning it, and the task a plan was split from is usually not the bot's
any more — so to a check that only reads the tracker's assigned/review lists they all look finished.
`TaskCleanupService`'s done-sweep did exactly that: it flipped such a workspace to `done` (grey tab, and its
card left in Waiting while its tab sat in the working tasks). It now also treats
`PlanQueueService.live_task_codes()` — the parent and every step not yet satisfied/cancelled, lower-cased —
as live (wired through `AgentService._live_plan_task_codes`); if the plans cannot be read the sweep skips
the cycle rather than guess "unowned". Anything else asking "is this task still wanted?" asks the queue by
code too. A restart still pauses every running plan (press Run), so a handed-back card says
"paused — press Run to hand it over again" while paused — `stepNote(step, planStatus)`; the server's
"it will be retried" is only true while the plan is running.

**Asking the plan's chat.** A sub-task's AskUserQuestion form offers "Ask UNA-1's chat"
(`ParentQuestionPanel` → `plan_question_routes.py` → `plan_queue/parent_question.py`): the questions
go to the chat the plan was split in (header `Kato plan queue · question from <id>`, labelled as
kato's there, pinned to `planQueuePrompt.js`), through the shared `chat_question.ChatQuestion` (the
split uses it too: wait for the chat to be free, send, read the reply), and the answer shows in the
form. The operator still picks and sends the answer — kato never answers an agent's question.

**Shared mode (YouTrack / Jira) — the tracker is the record, so a teammate and THEIR kato see the
same plan.** Offered at Approve only when the tracker can hold sub-tasks
(`IssueClientBase.supports_subtasks`) and the plan's ticket is a tracker ticket; the operator
CHOOSES it ("Create N sub-tasks" vs "Keep them in kato only") — never a default, it writes tickets.
- Approve creates one UNASSIGNED sub-task per step (`plan_queue/tracker_launcher.py`; unassigned
  is the fail-safe hold — pickup only takes tickets assigned to kato's user), titled
  `Subtask of UNA-1: …`, its description = brief + a footer (`step_footer.py`: part of / waits for /
  repositories by git-host slug / `plan-step:` marker). The driver posts ONE comment on the parent
  (`plan_record_on_tracker.py`): the record (`plan_queue_core_lib/plan_record.py`), REWRITTEN in
  place as the plan moves, never one comment per change.
- Status comes only from shared facts: the sub-task's state + assignee (tracker, one read per plan
  per 60s), its PRs by branch (= ticket id, counted from the ticket's CREATION, so a PR opened
  before its turn counts), and — for a Done ticket with no PR — the destination's history
  (`GitClientMixin.history_mentions`, anchored id). A step taken early or handed back moves the
  board; whoever has a sub-task is shown on its card; kato never assigns over a person.
- One DRIVER per plan (an install id in `<plan_queues>/host.json` + the token's user): only it
  releases (assigns to the user kato polls for). Every other kato FOLLOWS read-only; "Take over"
  republishes the record naming itself. Records are untrusted: author must be the driver user, and
  every task a real sub-task of the parent. A later record may change who waits for whom (the driver
  edited waiting tasks) only from the SAME driver, never in a take-over record, never for a done task
  (`plan_record.acceptable`); a follower takes it in `_follow_the_steps` (adds, drops, rewires).
- Every kato's pickup keeps the order (`step_gate.py`, checked FIRST in `process_assigned_task` —
  before triage and wait-planning, which clone and start a chat too, so a `kato:wait-planning` tag
  cannot jump it): a sub-task assigned before its turn waits until its parents are merged and done,
  with no clone, comment or state change (log: `not starting task X yet: it waits for …`). A ticket carrying the record never gets
  implemented whole again (`PLAN_RECORD_COMMENT_PREFIX` blocks execution). A sub-task works in
  its footer's repositories plus any its tags add (`_with_tagged_repositories`, below).
- Unverified against live servers (YouTrack sprint add, `"assignee": null` on create) — each
  client's report lists what to check. Jira Cloud REMOVED `/rest/api/3/search` (2025-05-01): every
  Jira search is `/rest/api/3/search/jql`, paged by `nextPageToken` (no `total`; a token handed back
  twice ends the walk) and always asks for `key` (the search answers with only the id otherwise).
  Written from Atlassian's docs, not yet run against a live Jira.

### First run (what a new user meets)
- **Repositories:** every repository under `REPOSITORY_ROOT_PATH` is one kato works in. There is NO per-repository
  approval — it was removed entirely (2026-11-04) because it blocked every new user and annoyed every old one;
  do not bring one back. The only ways to keep kato out of a repository are the two fields on Settings →
  Repositories (`panel: 'repositories'`): `KATO_IGNORED_REPOSITORY_FOLDERS` (not discovered) and
  `KATO_REPOSITORY_DENYLIST` (never touched). `SettingsDrawer initialTab` / `App.openSettings(tab)` open a tab
  from a message elsewhere (New task's "no repositories found" → Repositories).
- **Wizard:** the ticket step saves, then tests the connection (`POST /api/setup/test-tracker-connection` →
  `validation/tracker_connection_check.py`: kato's own validation in a CHILD process whose env is the saved
  settings — never mutate the server's env, setup mode snapshots/restores it) and shows the whole error on
  that step (Continue anyway allowed). The Claude step shows installed / signed in (`claude auth status` via
  `claude_core_lib/helpers/auth_status.py`, folded into the readiness probe as `logged_in`; None = cannot
  tell, never "signed out"). The folder step is the last before Finish. Finish says
  "Starting kato…" — never "all set" before the connection check passes; once running, a one-time
  "Kato is running — next steps" toast (`useSetupFinishedNote`).
- **A configured kato that cannot connect** sets `SETUP_KIND=cannot_connect` → the gate says so ("Kato
  can't reach a service it needs"), not "Welcome". Setup errors are passed WHOLE (no `[:500]`), shown in `<pre>`.
- **No task yet:** the tasks menu is held OPEN with a "Create your first task" button (it must not hide behind a
  hover); a tracker is named only when one is connected
  (`/api/config-status` `issue_platform`, labels from `constants/ticketSystems.js`).

### New task (local tasks — no tracker)
```
"New task" tab (UI: components/newTask/, draft in localStorage kato.newTaskDraft.v1)
  → POST /api/local-tasks {summary, description, repositories, start_mode, model, effort}
    (webserver/kato_webserver/local_task_routes.py)
  → LocalTaskService.create (kato_core_lib/data_layers/service/local_task_service.py):
      repos resolve + branch name checked BEFORE anything is written → store LOCAL-<n>
      as IN_PROGRESS → workspace record (the tab appears at once, "provisioning")
  → mode / model / effort overrides set (the CLI bakes them at spawn) → 202 {task_id}
  → background: WaitPlanningService.resolve_planning_context (clone + branch — the same
    hardened steps a kato:wait-planning ticket takes) → first chat turn with the opener
    (plan by DEFAULT; "implement"; "chat" = clone only, send nothing)
```
**The task's own definition can be read again** (report: "no way to see the original description"): the header's
task title is a button → a `Task` tab among the files (`taskDefinitionTab`, `TAB_VIEW.TASK`, `TaskDefinitionPane`)
← `GET /api/sessions/<task>/task-definition` (`task_definition_routes.py`), read from the workspace record — the
same one the agent's Task definition is built from. On demand only: the 5s tab list still omits the description.

**A chat's opener never named the branch it was already on** (`resume_session_for_chat`, used by every chat-started
spawn — "New task"'s Plan and Implement, a `kato:wait-planning`/`kato:wait-editing` hold): only the one-shot
implementation prompt named it, via `repository_scope_text`, which reads a destination off repository OBJECTS a chat
never has — only bare paths. Plan mode, asked to propose a plan with nothing naming the branch, told the operator
none was set even though one was given and the orchestration layer had already checked it out on disk (the
checkout itself was never wrong; only the agent's account of it). `workspace_inventory_block` (agent_core_lib) now
takes `branch_name` and names it right after the repository list, in BOTH the first-spawn block and the
resumed-session scope reminder (re-sent every turn, like the repo list); the webserver's `_chat_branch_name`
resolves it via `repository_service.task_branch_name(task_id)`, the same kato-tags-based answer the actual
checkout uses, so the two can never disagree.

**Attachments at creation — "New task" acts as the first prompt, so this is the one chance to hand over documents**
(a spec, a CSV, a PDF: report, "task creation itself acts as the first prompt"). The pane's `NewTaskAttachments`
(drag/drop + a picker, files held in `NewTaskPane` and persisted across a tab switch in IndexedDB —
`newTaskAttachmentDraft.js`, the same idiom as the composer's own image draft, because the pane UNMOUNTS on every
tab switch while the rest of the draft survives even a reload) → `createLocalTask(body, files)` goes multipart only
when there are files (`payload` = the same JSON body, stringified, + one `attachments` field per file — the common,
no-attachment case stays plain JSON). `POST /api/local-tasks` saves each one into the new task's `attachments/`
folder — reusing `save_attachment`, the SAME folder a ticket's downloaded screenshots use — synchronously, BEFORE
`starter.start()` even begins the background clone+spawn thread: no race against the upload. `LocalTaskStarter`
then lists that folder (`list_attachment_paths`) and prepends `agent_prompt_utils.task_uploaded_files_block` (a
generic cousin of `task_attachments_block` — these can be any document type, not only images, so the wording never
assumes a screenshot) ahead of the opening message. Only for Plan/Implement: "Just open the chat" sends nothing on
purpose, so an attached file there is saved but not auto-mentioned — the operator's own first message covers it.
**Pasted or dropped anywhere on the pane counts** (report: "the chat does not let me upload any files or images in the
prompt of the task"): the description box's paste (a screenshot, a file copied in the file manager) and a drop anywhere
on `#new-task-pane` go through the same `attachFiles` as the picker; a drop the Attachments section already took is not
added twice (it calls `preventDefault`, the pane skips a prevented drop). A drop used to let the browser open the file
over the draft. **The chat composer saves any other file too:** an image the model can see (`isInlineImage`: type +
5 MB) is a thumbnail, text is inlined, and EVERYTHING else — a PDF, a spreadsheet, a zip, an oversized image, "text"
that turns out binary — goes through `uploadAttachment` into `attachments/` and is named in the message
(`formatSavedFile`). It used to be refused ("Only PNG… supported").

**A chat needs git, not a pull request API.** Preparing a chat's repositories (`WaitPlanningService._prepare_repositories`,
so wait-planning and every local task) passes `pull_requests_required=False`. It used to require a known host and a
token, so a repository on any other host got no branch at all: the clone sat on `master` and the agent worked there.
A ticket kato runs by itself still requires it (`prepare_task_repositories` default): it ends in a PR, so it fails up
front. Tests must not stub `_prepare_repository_access` around this; a stub hid exactly this. A local task that cannot
be prepared says so in its preparation log (`LocalTaskService.prepare`), or the UI just stops at "starting agent".

**Which host a repository's pull requests live on** (`RepositoryInventoryService._resolved_pull_request_provider`): the
words in the remote were the only clue, so an SSH alias failed (`git@work:team/api.git`: no provider;
`git@github-work:…`: the API address `https://github-work/api/v3`). Now, in order: the repository's own provider, then the real
host (`_remote_for_addresses` → `git_core_lib` `ssh_alias_utils.remote_with_real_host`, `ssh -G`, which connects to nothing), then
a `*_API_BASE_URL` on the same host (`_provider_at_host_of`: the operator already said what a self-hosted host is). The result is
only for building addresses: **`repository.remote_url` is never rewritten**, because `_assert_remote_is_the_configured_one`
compares it before every push. The stub `_resolve_publish_context` builds for a repository out of the list has no remote, and
**must not read one from the clone**: `.git/config` is agent-writable, and the provider token goes to whatever that names. The
error names the host, or says the repository left the list: they read alike in a log and need different fixes.

**The UI shows that branch, not the id** (report: "the branch name is not reflected anywhere"): the tab list sends
`branch_name` (`_workspace_record_to_dict` → `task_branch`) — the tab tooltip's Branch row, the header chip
(`#session-task-branch`, only when the branch is not the task id — `utils/taskBranch.js`), and the Files tab's repo
chips, which compare against it (`branchWorthShowing(branch, taskId, taskBranch)`: they matched the task id, so every
repo of a custom-branch task got a chip). The plan board says what its sub-tasks merge into (`merge_branch` on the
plan payload). A chat respawned by `MainChatDelivery.spawn` (review loop, comment runs) now names the branch too —
it passed none, so its opener named no branch and `expected_branch` was recorded empty, for every task.

**A task's branch has ONE answer: `kato_tag_utils.task_branch(task_id, kato_tags)`** (reached as
`RepositoryService.task_branch_name` / `build_branch_name`): the name in a `kato:branch:<name>` tag in kato's
OWN tags (workspace metadata), else the task id. "New task" takes an optional branch name; empty changes nothing.
Code holding only a task id must ask, never use the id as the branch: the boot reconcile and the Changes tab's
self-heal force a clone onto "the task's branch", so a stale `task_id` there would strand the work on the wrong
branch with no error. `task_branch_choice.unusable_branch_reason` (+ `branch_name_problem`, git_core_lib) refuses a bad
name BEFORE anything exists: not a valid name, the branch a repo merges into (the work would land on it with no PR),
or one another task has. A branch already on the remote is continued. A ticket can name its branch too, and gets less,
because anyone who can edit a ticket can add a tag and the branch is where kato pushes: `kato:branch:<name>` on the
ticket is copied into kato's own tags (`record_branch_from_ticket`, in the provisioning pass that clones) only if it
names a NEW branch, before the task started, and not on a plan step (plans find a step's PR by ticket id, on every
kato following the plan). Anything else: the tag is left out, the task runs on its id, and its preparation log says
why. A rename (`TaskBranchRenameService`, the header's pencil among the git buttons) is `git branch -m` in every clone
+ one tag, AND on the remote where the branch was pushed: git has no remote rename, so the commit the OLD REMOTE branch
holds is pushed under the new name (`push_commit_as_new_branch`, empty lease = must not exist, even a fast-forward is
refused), then the old one is deleted (`delete_remote_branch`, lease = the commit checked: anything pushed since stays).
Never a push of unpushed work: local commits stay local until Push. Refused, changing nothing anywhere: an OPEN pull
request from the branch (deleting its branch closes it on GitHub, strands it elsewhere), a PR API that cannot be asked
(`pull_request_states` raises, never `[]`), commits only the remote has ("pull first"), a remote that cannot be asked,
the new name already local or remote, while the agent works, on a plan step. Local renames + new-name pushes + the tag
are all or nothing (rollback deletes the new remote branches it made); deleting the old remote branches comes after,
and a failure there is listed in `problems` (red toast), never hidden. The name lives in the workspace, so anything that must find the PR after the forget
flow deletes it reads the branch first: `ReleaseNoteSource.branch`, then `KatoPullRequestProbe.pull_requests_of_branch`.

**The ONE place local tasks are told apart:** `local_task_routing.py`. `LocalAwareTaskService` /
`LocalAwareTaskStateService` (wired in `kato_core_lib.py` in place of the plain services) send
every call about a `LOCAL-*` id to the store and add local tasks to every list in the matching
state (all list methods funnel through `get_assigned_tasks(states=…)`). Do NOT add
`is_local` checks elsewhere. Leaving local tasks out of the lists is not neutral: cleanup would
mark their workspaces done and their PR review comments would never be polled. A local task is
created IN_PROGRESS — as OPEN the autonomous scan would pick it up and implement it unasked.

**No tracker at all** (`KATO_ISSUE_PLATFORM=local` — the wizard's "Skip for now" on its first step,
or Settings → Task provider → No tracker): `validate_env`
requires no tracker keys, and `KatoCoreLib._build_ticket_client` puts `NoTrackerClient` (empty: lists
nothing, validates nothing, refuses writes for a non-local id) under the SAME LocalAware services, with
the `kato.local_tasks` config block supplying only state names. So local tasks stay the one routing
path — do not add a separate "no tracker" branch elsewhere. An UNSET platform still means YouTrack.

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

**Which PRs a task has is asked of the host through ONE lookup, rarely:** `pull_request_lookup.py`
`PullRequestLookup`, behind `RepositoryService.find_pull_requests` / `pull_request_states` (the one host call is
`RepositoryService._ask_host_for_pull_requests`; `tests/test_pull_request_lookup.py` `SingleLookupGateTests` fails
on any other). The review scan used to search the host once per repository of every In Review task whose ticket
links no PR, every 50s — UNA-3211's twenty repositories were twenty Bitbucket calls a scan, until it answered only
429. Now: an answer (a PR, or none) is remembered `ANSWER_TTL_SECONDS` (600) per (repo, branch, title prefix,
state); a PR kato opens clears its branch at once (`create_pull_request` → `forget`), one opened outside kato is
found within the TTL; a 429 pauses EVERY lookup on that host (`Retry-After`, else 120s, max 900s) — a remembered
answer, even an old one, is used while paused, else `LookupPaused` ("could not ask", never "no PR"). The scan
handles each repository on its own: a host error is one warning line (never a traceback that dropped the task's
other repositories) and paused repositories are summed up in one line per scan. `cached=False` (the operator's
Create PR — it must not open a duplicate — and the merge probes via `pull_request_states`) always asks, but never
during a pause; Create PR then reports the repository failed instead of creating. Comment listing on a known PR
still runs every scan.

### Safeguard-flag fallback (retry on a lower model version)
When the API's safeguards refuse a turn (`... safeguards flagged this session ... Details:
[cyber]` — a server-side AUP refusal, not a transport error; the SAME prompt fails the same way
on that model), kato offers a one-click retry on a pinned LOWER model, like VS Code does.
- Detector: `claude_core_lib/helpers/safeguard_error.py` (generic, kato-free) — matches the AUP
  link + "flagged this session"; mirrored in `webserver/ui/src/utils/safeguardError.js` (keep
  the anchors in step).
- Fallback model: `KATO_CLAUDE_FALLBACK_MODEL` (default `claude-opus-4-8`; a PINNED id, NOT an
  alias — an alias always resolves to the latest, i.e. the flagged version; empty disables the
  offer). Bridged to `app.config['SAFEGUARD_FALLBACK_MODEL']`.
- UI: `SafeguardFlagBanner` above the composer (shown only when the LATEST turn is a live flag
  AND a fallback is configured) → `POST /api/sessions/<id>/retry-on-fallback`, which pins the
  fallback as the task's model override and respawns + resends the flagged message (the last
  user turn, read before the respawn). Operator-triggered, never automatic — it is a recovery
  for a false-positive flag, not a silent safeguard bypass.

### Task tags kato owns (repo + model)
```
"+ Add repository" (Files tab) → TaskRepositoryService.add_task_repository
   1. WorkspaceService.add_tag(task, 'kato:repo:<id>')   ← kato's OWN store (.kato-meta.json `tags`): SOURCE OF TRUTH
   2. sync_task_repositories (clone + task branch)
   3. _mirror_tag_to_ticket → task_service.add_tag        ← best effort; result `ticket_tag`
RepositoryInventoryService.resolve_task_repositories reads tracker tags ∪ those kato recorded
   (attach_kato_task_tags, wired in kato_core_lib.py) — one choke point for all 8 resolve callers.
```
**The add must never hinge on the tracker.** Step 3 reports `{status: added | unsupported | failed,
error}` and never fails the add: bounded by `_TICKET_TAG_DEADLINE_SECONDS` (20s), carries the tracker's own
error text, and re-adding the repo is the retry. The UI toast lists a failed ticket tag FIRST and never expires.
Per tracker: **YouTrack** attaches a tag by its database id, never by name (`{"name":…}` is the 400 from
UNA-3264) — `YouTrackClient.add_tag` finds the tag among those the token sees (client-side and case-insensitive, as a
precaution against `:` in `query=`), creates it if missing, then attaches by id; the tag is private to the token's user.
**Jira / GitHub / GitLab** labels are free-form and auto-created. **Bitbucket** issues have no tags
(`supports_tags = False` → mirror skipped: its `add_tag` borrows the single-valued `component` and would
overwrite one set by hand), and so does an install with no tracker. `supports_tags` lives on
`IssueClientBase` and reaches kato via `TaskDataAccess` → `TaskService.supports_tags(issue_id)`.
`WorkspaceService.create` preserves `tags` (it is called on every provisioning pass).

**A plan sub-task's footer is where it starts; tags only add to it.** A sub-task always works in its
footer's repositories (every kato can read the footer; the list the operator left it in the plan — see Plan
queue), and `kato:repo:` tags from the ticket AND from THIS kato join that set (`RepositoryInventoryService._with_tagged_repositories`). The footer used to win outright
over the tracker's tags, so a repo tagged on the YouTrack sub-task was never cloned, and "+ Add repository"
saved its tag, cloned nothing, and the toast said "repository was already cloned" (UNA-3272 + event-core-lib).
Another kato may not see a tag or may name the repo differently, so a tag that matches nothing is left out
with one warning, not fatal; a tag on an ignored folder is refused as on any ticket. Tags only add: taking
one off does not remove a clone. The add flow now also refuses to call itself done: when the sync names the repo in neither
`added_repositories` nor `already_present`, the add is reported as a failure (`_require_repositories_on_task`).

**A repo added to a running chat needs no restart.** A session's sandbox is fixed at spawn, but it is the task folder
(`effective_sandbox_roots`: cwd, `--add-dir`s, cwd's parent), and every clone lands in that folder. `requires_session_restart`
(`_live_session_missing_paths`) asks that same boundary, and "inside", never equal: an exact-path compare told the operator to
restart for every added repo. Proven with the real CLI and a real `StreamingClaudeSession`: a folder created after the spawn was
written to, `outside_sandbox=False`, with or without `--add-dir`. Prove such a claim that way: under `claude -p` there is no
approval tool, so kato's catch-all `ask` rules turn EVERY write into a refusal, even inside cwd. `--add-dir` is not what limits
writes either: those `ask` rules are, answered by kato. So every write raises a `control_request`, inside the folder too
(`ask` beats `allow` in the CLI); only `outside_sandbox` differs.

**"Edit automatically" is kept by kato, not the CLI.** Since every write is a `control_request`, each one used to stop for a
click, and a task with no one watching stopped at its first file. `_maybe_auto_resolve_pending` now answers a write when
`_is_task_folder_edit` says so; it runs after every carve-out, and a remembered "Deny always" wins. All of it must hold: the
session is on `acceptEdits` AND the task's next mode is too (picking Manual counts at once), the Action Guard gave a clean verdict
(no verdict is not clean), and `plain_write.is_plain_write`: a write tool, every path inside the task folder ON DISK (`realpath`:
a repository can carry a symlink out, so the lexical `outside_sandbox` is not enough to approve on), and no control file:
`.git`, `.claude`, `.vscode`, `.idea`, `.mcp.json`, `.gitmodules`, … at any depth, or kato's own `.kato-*`, which hold the
task's branch and repositories. Those decide what runs next with no approval after the write, so they keep asking. It goes through
`_resolve_permission_decision` like a click (hook, audit). No browser: the 0.25s `permission_auto_resolver` tick answers it.
Proven with the real CLI, a real `StreamingClaudeSession` and this path: 3 plain writes through, 6 others stopped, Manual stops all 9.

**"+ Add repository" takes several at once** (`POST …/add-repository {repository_ids: [...]}` →
`TaskRepositoryService.add_task_repositories`; the single `repository_id` form is a one-item wrapper with its
old reply shape). Every tag is recorded in kato FIRST, then ONE sync clones them together (parallel), then each
tag is mirrored onto the ticket (YouTrack tag by id / Jira label; concurrent, bounded per tag). A repo that
cannot be added goes in `rejected` without stopping the rest; each repo's own `ticket_tag` outcome is reported
(a refusal never fails the add, and the toast lists it first and never expires). The picker
(`SearchPickerModal multiple`) keeps its ticks independent of the filtered list — filter, tick, filter again —
and lists the picks above the list, so a pick the search hides is still visible and removable.

`kato:model:<family>[:<version>]` on a ticket picks the task's STARTING Claude model: `kato:model:sonnet`
= the alias (the CLI runs the latest), `kato:model:sonnet:5.5` = pinned `claude-sonnet-5-5`
(`kato_tag_utils.model_from_tag`; families `fable|opus|sonnet|haiku`; a malformed tag is ignored and
warned ONCE). `WaitPlanningService.observe_model_tag` records it per task (`task_model_tag_store`,
`~/.kato/task_model_tags.json`) from the same two places the planning hold is observed; the spawn funnel
`PlanningSessionRunner.start_session` applies it: **explicit pick (chat picker) > tag > configured
default**, and never to a chat that is on another agent. The picker's `GET /api/sessions/<id>/model`
answers the tagged model so the UI shows what will run.

### Picking a Claude model in Settings (older plans run older models)
Settings → Claude agent → "Model override" and "Safeguard fallback model" are DROPDOWNS (`picker: 'claude'` →
`ModelPickerField`, fed by `GET /api/claude/models`, `claude_models_routes.py`): "Not set" (`empty_label`), then
"Always the newest" (the aliases), then "Specific versions" (`model_catalog.discover_pinned_models`: every model
the PRICE TABLE knows — `model_pricing.priced_model_ids`, so a fresh install offers the older ones with no second
list — plus any version the account's sources name: the models API, the CLI config's offered options, the session
logs; a `[1m]` marker dropped, the undated id preferred; a gated family (fable) only once confirmed), then "Other —
type a model id…", which opens a box (picking it never saves the marker). EVERY `--model` the claude lib builds goes through `helpers/model_ids.model_flags`, so "Sonnet 4.6" /
"sonnet:4.6" / "claude sonnet 4.6" reach the CLI as `claude-sonnet-4-6` and a bare family as its alias; anything
else (a dated id, Bedrock, `[1m]`) passes as written. The CLI's own "There's an issue with the selected model" was
what a teammate saw for "sonnet 4.6". Do not add a fourth `--model` builder outside `model_flags`.

### Usage dialog (the rail footer's chart button → `GET /api/usage`)
Tokens per task, read from the Claude transcripts (`claude_core_lib/helpers/transcript_usage.py`), summed
over EVERY chat the task had (current + `previous_session_ids`). **Count each API message once, by
`message.id`** — Claude logs one line per content block and repeats the whole `usage` on each (7,169 lines
for 3,026 messages measured), so summing lines overstates ~2.4x. Cache reads are >90% of the total and are
shown apart from output and fresh input, never folded into "input". `TranscriptUsageReader` parses only the
bytes appended since the last read (a 185 MB transcript: 0.4s cold, ~5ms warm). Codex chats are named as
uncounted, not silently dropped. Not polled: read when the dialog opens and on Refresh.

**Deleted tasks are kept as numbers, because their transcripts are not.** Deleting a task
(`DELETE /api/sessions/<id>/workspace`) erases its Claude transcripts on purpose, and the dialog counts
FROM transcripts — so a deleted task would vanish and the grand total shrink. Before the delete, the route
snapshots the task's usage (`usage_routes.snapshot_task_usage`, read through the same cached reader) and,
only once `terminate_session` really removed the transcripts, keeps it in `~/.kato/usage_history.json`
(`helpers/usage_history_store.py`, `KATO_USAGE_HISTORY_PATH`). **Numbers only** — never a prompt, reply or
path — and a snapshot that fails never blocks the delete. `/api/usage` lists them APART (`deleted_tasks`,
`totals_with_deleted`; `totals` stays what exists now) and the dialog shows them behind "Include deleted
tasks" (default off, remembered in `kato.usageShowDeleted.v1`). A failed terminate keeps NOTHING: its
transcripts are still counted under the live task, and a second copy would count the same tokens twice.
Tasks deleted before this existed left nothing and cannot be recovered. Rows carry `created_at` (earliest
`created_at_epoch` of the workspace/session record, else the first turn) beside `usage.last_at`.

**A row leads with TOKENS and one plain bar; dollars are in the details.** The bar's length is the
task's tokens against the biggest task's (`usageBarPercent`) — one colour, no segments. It is NOT progress
toward a limit (kato knows of none), and it was never split into green/blue/grey again after that confused
the operator twice: the split is nearly identical for every task (cache re-reads are ~96% of the tokens), so
the colours only drew one thin sliver to puzzle over. **The bar must never depend on a price** — it once did,
and an older kato (no `cost` in `/api/usage`) drew every bar empty, because the rebuilt UI reloads itself
before the backend is restarted. Cost is OPTIONAL everywhere on the page (`hasCost`): absent → no `$`, no
Cost column, no false $0.

The money — `cost` — is priced once, on the server. `claude_core_lib/helpers/model_pricing.py` is the ONE
price table (dollars per MTok; `PRICES_AS_OF` is the date of the rates — change both together).
`TranscriptUsageReader` prices EACH MESSAGE at its own model's rate and keeps `cost` {output, fresh,
cache_read, total} per model and in total — **per message, never from totals**: Haiku 5.5 has a second rate
card above a 100K-token prompt, a cache read is 0.1x input on most models but 0.05x on Opus 5.5 and 0.025x
on Fable 5.1 (so never a shared multiplier), and a cache write is 1.25x (5m) or 2x (1h) — the split is read
from the transcript's `cache_creation`, and is PART of `cache_write`, never added to the total. A model with
no entry is listed in `unpriced_models` / `priced: false` and is NEVER counted as $0: the details show `$X+`
or `—`. A mid-task model switch needs no special handling — every message carries its own `message.model`, so
the task simply has two model rows (each with `first_at`/`last_at`, shown in first-use order, plus chips on the
row). The route adds `label` per model via `kato_core_lib/helpers/model_label_utils.py` (the one name helper)
so the page never formats a model id. Figures are API-equivalent ESTIMATES (a subscription is not billed per
token) and the footnote says so. Do not add a second price table or compute cost in JS.

### False-success guards (important — previously bugs)
- **A finished background job never holds a chat busy.** A session is "working" after its turn ends only while a
  background job it started (`run_in_background`, Monitor, Workflow) has not REPORTED finished — ONE rule, two
  copies pinned equal: `claude_core_lib/session/background_jobs.py` (`BackgroundJobs`, the server's `is_working`,
  caught up incrementally from the append-only event log) and `webserver/ui/src/utils/backgroundJobs.js` (the
  reducer's `awaitingBackground`). Both run `tests/fixtures/background_jobs_cases.json`
  (`tests/test_background_jobs_parity.py` runs the JS with node) — add a case there, never to one side. The CLI
  (2.1.322) reports jobs as `system/task_started` (`tool_use_id`, `is_backgrounded`), `task_updated` (`patch.status`),
  `task_notification` and `background_tasks_changed` (its own list of what runs); an older CLI only as a
  `<task-notification>` user message. It used to be "the last turn used a background tool": UNA-3416 started an
  `npm install` in the background, the job reported back 25s later INSIDE the same turn, the turn ended — and the
  session read working for 11h: kato's own run waited out its 30-min idle limit and three review loops each
  waited 2h ("the chat was not free for 7200s"). A job outlives a newer turn (the operator asking "still
  running?" must not drop it); only its report, the session closing, or the server's busy correction ends it.
- **Every ticket move goes through ONE service: `task_moves.py` `TaskMoves`** (In Progress / In Review / Open /
  Done, every tracker and local tasks — the state service below picks the configured field and state, the client
  only makes the call). `move` raises (kato's own start — a ticket left in the queue would be started twice — a
  publish, an operator's Done); `try_move` logs and answers False (a planning chat, a failed run's way back to Open).
  Never call `move_task_to_*` / `move_task_to_state` / `move_issue_to_state` anywhere else:
  `tests/test_task_moves.py` `SingleMoverGateTests` reads the source and fails naming file:line. Done still announces
  through `TaskStateService.move_task_to_done` (one observer list). What it fixed: In Progress had three copies,
  and the gaps left tickets in Open while kato worked — a planning ticket moved only after the clone + spawn (now
  FIRST, and also when its chat already runs), and a failed run moves its ticket back to Open
  (`handle_started_task_failure`) while the operator's "continue" moved nothing: the queue scan now runs
  `move_if_agent_working` (in `process_assigned_task`, after the plan-step gate) — a QUEUE ticket whose agent is
  mid-turn (`running_agents.is_mid_turn`) moves and nothing else starts on it. Only WORK moves it: an idle chat left
  after a failed run does not undo the failure handler's Open. Tests: real `LocalAwareTaskStateService` +
  `LocalTaskStore`, every real caller, 100% coverage, no mocks. The pattern is AGENTS.md → "One capability = ONE
  service, tested, and GATED".
- **A chat's own `<KATO_TASK_DONE>` is not proof the operator signed off.** The sentinel callback
  (`AgentService.finish_from_done_marker`, wired onto every spawned session — chats included) used to
  publish unconditionally once off the autopilot/review-loop branches: push, open a PR, move to review,
  with no button pressed and no `kato:autopilot` tag, on a `kato:wait-planning`/`kato:wait-editing` chat
  the operator was driving by hand. The system prompt says "print it only once the operator has said
  we're done" — a go-ahead to *start* ("let's do it please") read back by the model as a sign-off on the
  *result* is exactly the kind of thing a soft instruction cannot stop, and it did not: it pushed an
  unreviewed branch and opened a PR, no autopilot, no click (a real `kato:wait-editing` task, confirmed
  on the operator's own tracker as a bare push, not a tracker-side merge automation). `finish_from_done_marker`
  now runs any non-autopilot done token through `_chat_done_marker_should_wait` — the exact same
  `_should_pause_for_push_approval` gate (`KATO_AUTO_PUSH_ENABLED` default off, `kato:wait-before-git-push`
  override) the autonomous flow already ran, never past it before. An unreadable task holds too, same reasoning
  as autopilot's own "could not read the task" case: push/PR stay an operator action until the gate is proven
  to allow them. `push_approval_gate_utils.py` now documents both callers.
- **Deleting a task warns first when its work was never pushed.** The tab's ✕ opens `UnpushedWorkWarning`
  BEFORE `ForgetTaskModal`: `GET /api/sessions/<id>/unpushed-work` → `TaskPublishService.task_unpushed_work` →
  `GitClientMixin.work_not_on_remote` per clone (uncommitted files on ANY branch + `rev-list --branches --not
  --remotes`: commits on any local branch no remote has). Nothing to lose → straight to the delete dialog, no
  flash (300ms grace before "Checking…"). Otherwise "Work not pushed" names each repo, with Cancel (focused) /
  Open task / Delete anyway…, and the delete dialog's red callout names the same repos. A clone git cannot
  read in 15s, a failed request, or no service = "could not check" → warns, NEVER "clean". Not
  `has_changes_to_push`: that is a button hint with a 3s budget where False means "none found yet".
- Task: all repos unchanged → `NO_CHANGES` status, task NOT moved to "In Review"
- Review answer: thread never auto-resolved, reply always prefixed with visible "no code changed" disclaimer
- **Green means the work landed.** `_push_problems(push_result)` is the ONE answer to "what did not reach the remote":
  a failed push, or a skip with `problem: true` (any reason but `NOTHING_TO_PUSH`). Update source refuses such a repo
  (`failed`, source untouched) and Done counts it as a failure; a PR skipped as empty is not a success. Git refusing
  the source switch is `blocked`, never `updated`. A source kato cannot find is `failed`, never `skipped`.
- A toast's colour is decided beside its title, in `sessionHeaderFormatters.js` (`kind`), never in the component: a
  "Source not updated" box came out green. **A failed pull is a failure, never a skip** (`pull_task`): filed as a
  skip it read as a green "Nothing to pull" over git's errors. Only `already_in_sync` / `remote_branch_missing`
  skips may leave the Pull toast green (`HARMLESS_PULL_SKIPS`). Summaries list every problem; one repo pushing must not hide another's failure.
- **A PR that cannot be created says why.** `RepositoryPublicationService.create_pull_request` sets the PR API
  up ITSELF (`_prepare_pull_request_api`, raising the real cause: no token, no Bitbucket API email…). It used to
  rely on the lookup before it, which swallows its failure (logged once per process, then "no pull requests"), so
  every cause read "incomplete pull request configuration" — the calendar/audit-core-lib reports of 2026-11-03.
  That message now names what is missing; an owner/name missing from the inventory is read off its configured
  remote (never the clone's). Provider API settings are read at boot: change a token or email → restart.
- **A refused tool is never silent.** The CLI refuses some tool calls WITHOUT asking (a background subagent cannot
  ask; a remembered "Deny always" answers): they arrive as the result's `permission_denials`, which nothing read, so
  the agent retried until "Reached maximum number of turns" while the chat showed only its own "no permission to
  edit". Every turn now lists them (`refusedToolsText`, a SYSTEM bubble). A turn limit is only ever there because
  one was set — `KATO_CLAUDE_MAX_TURNS` (per REPLY, chats included; empty = none) or an inherited
  `CLAUDE_CODE_MAX_TURNS` — and the max-turns bubble says where.
- **A failed turn says why.** `failure_reason` (`result`, else `errors`, which a turn that fails during execution
  sends with no `result`), then `failure_detail` (the last stderr lines, attached before the event is published),
  then `resultFailureText`'s words per subtype. Never the bare word "failed".
- **A ticket move is verified, not just POSTed.** `TaskStateService.move_task_to_done` (and `_in_progress`/
  `_review`) resolve to each tracker client's `move_issue_to_state`. YouTrack's already re-read the field after
  writing it (`_assert_issue_state`). `JiraClient.move_issue_to_state`'s custom-field branch did too — but its
  `status` branch (the native workflow transition, and Jira's `done_state_field` DEFAULT per
  `kato_core_lib.yaml`, so this was the common case, not an edge one) only checked `raise_for_status()`: a
  transition a workflow post-function or screen then silently undid read back as a success. It now re-fetches
  `status` after the transition POST and raises if it didn't actually move, same shape as the other branch.

---

## Config

```yaml
kato:
  task_scan:
    scan_interval_seconds: 50  # main.py → DEFAULT_SCAN_INTERVAL_SECONDS. 0 = manual-only.
```

Auto-discovery: if `REPOSITORY_ROOT_PATH` is set (no explicit `repositories:` list), Kato walks the tree for `.git` folders. Result cached after first run. Background warm-up runs this at boot.

**One bad folder must never take the whole list down.** `_validate_inventory` raises on every read, so a duplicate id
cost EVERY repository its source folder (Update source: "duplicate repository id: <a repo the task never used>") and,
through the `_resolve_publish_context` stub, its pull requests ("unable to determine pull request provider"). So the
walk skips the workspaces folder (task clones are copies, not repositories, and a root holding `~/.kato` found every one)
and `_one_repository_per_id` keeps the folder closest to the root, the one `kato:repo:<id>` finds at `<root>/<id>`, and
warns naming both. A duplicate in a hand-written `repositories:` list still raises: that is a mistake to stop for.

---

## Testing Patterns

- All `unittest.mock.Mock` + `SimpleNamespace` — no network, no DB
- Each core-lib has its own `tests/` folder inside it
- Top-level `tests/` is for kato integration tests only
- Key test files: `test_task_publisher.py`, `test_startup_validator.py`, `test_repository_connections_validator.py`, `test_review_comment_question_mode.py`
- **Never hand a mock process to a real kill.** `int(MagicMock())` is 1: three tests sent SIGKILL to pid 1 and
  process GROUP 1, which killed GitHub's runner ("the hosted runner lost communication"). The kill helpers now
  refuse pid/group ≤ 1 (`process_liveness.kill_process_tree`, `streaming._process_group_of`); mock the kill anyway.
- **A file-backed cache keys on `utils_core_lib.file_snapshot`, never on mtime alone.** Linux stamps file times
  from a coarse clock, so a rewrite in the same tick keeps `st_mtime_ns` and an mtime-keyed cache serves stale
  data (a comment marked FAILED read back IN_PROGRESS 37/40 on Linux; never on macOS). Signature = mtime+size+inode,
  and a read of a file changed in the last 2s is not cached.
- **No agent CLI gets the operator's terminal.** Every `claude`/`codex`/`grok`/`opencode` launch passes `stdin=DEVNULL`
  (or pipes its input). Given a TTY stdin, `claude --help` switches the terminal to raw mode
  (`-isig -icanon -echo`, `opost` kept — libuv's fingerprint) while it runs, and a probe killed by its
  timeout LEAVES it raw: Ctrl+C then sends no SIGINT ("ctrl+c doesn't stop kato") and the shell has
  no echo after exit. Pinned by `tests/test_agent_cli_never_gets_the_terminal.py` (real pty).
- **CI ≠ the local gate.** The CI step stops at its FIRST failing command, so one top-level failure hid every
  lib suite from Linux for weeks. To vet CI, run `.github/workflows/ci.yml`'s commands on `git archive HEAD` in a
  `python:3.11` container as a non-root user with an empty HOME.
