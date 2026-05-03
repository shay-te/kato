# Setup — kato

Fast path from a fresh clone to a running kato. The full reference lives in [README.md](README.md); this file is the minimum a new operator needs.

---

## 1. Prerequisites

| Tool | Why | How |
|---|---|---|
| Python 3.11+ | kato itself | `python3 --version` |
| `git` | clone repos | `git --version` |
| `make` | bootstrap helpers | usually preinstalled |
| `node` + `npm` (optional) | rebuild the planning UI bundle from source | only needed if you change `webserver/ui/` |
| `docker` + `docker compose` (optional) | the OpenHands backend or `make compose-up-docker` | not needed for the Claude backend running locally |
| Claude Code CLI (optional) | the Claude agent backend | https://docs.claude.com/en/docs/claude-code/setup |

You need exactly **one** agent backend installed: either the Claude CLI (recommended for local development) or Docker (required for OpenHands). Pick one.

---

## 2. Bootstrap

The canonical entry point is a Python script that works the same on Linux, macOS, and Windows.

**macOS / Linux** (with `make`):

```bash
make bootstrap
```

**Windows** (PowerShell or `cmd.exe`) — or any OS where you don't have `make`:

```powershell
python scripts\bootstrap.py
```

Both paths run the same script. It creates `.venv/`, installs kato + the webserver in editable mode, builds the planning UI bundle if `npm` is available, runs the test suite, and copies `.env.example` to `.env` if you don't already have one.

If anything fails, the script prints exactly which step broke. Fix that and rerun bootstrap.

---

## 3. Configure `.env`

You have two paths. Pick one.

### Option A — interactive configurator (recommended)

```bash
make configure
```

Prompts you through every required setting (issue platform, agent backend, credentials, repository root). Writes the result to `.env`. Safe to rerun.

### Option B — edit `.env` by hand

Open `.env`, fill in the blanks. The minimum to start:

| Variable | What |
|---|---|
| `KATO_ISSUE_PLATFORM` | `youtrack` / `jira` / `github` / `gitlab` / `bitbucket` |
| `KATO_AGENT_BACKEND` | `claude` (local) or `openhands` (Docker) |
| `REPOSITORY_ROOT_PATH` | absolute path to a folder that contains your repo clones |
| Issue-platform credentials | `YOUTRACK_TOKEN` / `JIRA_TOKEN` / `GITHUB_API_TOKEN` / `GITLAB_API_TOKEN` / `BITBUCKET_API_TOKEN` — only the one for the platform you picked |
| Agent-backend credentials | Claude: `CLAUDE_CODE_OAUTH_TOKEN` *or* `ANTHROPIC_API_KEY`. OpenHands: `OPENHANDS_LLM_API_KEY` |

Everything else has a sensible default.

### Validate the config

```bash
make doctor
```

Prints a green check or names exactly which variable is missing. Run this any time `.env` changes.

---

## 4. Run

Pick the runtime that matches your `KATO_AGENT_BACKEND`:

| Backend | macOS / Linux | Windows | What it does |
|---|---|---|---|
| `claude` | `make compose-up` | `python scripts\run_local.py` | Runs kato locally against your `claude` CLI. Recommended. |
| `claude` | `make run` | `python scripts\run_local.py` | Same as `compose-up` for the Claude backend — alias for muscle memory. |
| `openhands` | `make compose-up-docker` | `docker compose --profile openhands up --build` | Brings up kato + OpenHands containers via `docker compose`. |

The first scan tick fires after a short delay (default 5s, configurable via `OPENHANDS_TASK_SCAN_STARTUP_DELAY_SECONDS`). After that kato scans every 60s.

---

## 5. Verify it's working

Three signals:

1. **Terminal**: scan-loop log lines like `Scanning for new tasks and reviews` / `Scan complete` / `Idle · next scan in 55s`.
2. **Planning UI**: opens automatically at `http://127.0.0.1:5050`. The status bar at the top mirrors the terminal in real time. The history dropdown (▾) shows recent activity.
3. **First task**: assign a ticket to the user named in `YOUTRACK_ASSIGNEE` / `JIRA_ASSIGNEE` / etc., then watch a tab appear in the planning UI when the next scan picks it up.

If the planning UI stays on `Connecting to kato…` for more than a few seconds, the webserver isn't reaching the orchestrator — check the kato terminal for tracebacks.

---

## 6. Pick your platform — quick choices

These are the decisions most operators get stuck on. The full per-platform setup lives in [README.md → Third-Party Setup](README.md#third-party-setup).

**Issue platform.** Each platform needs (a) a bot/user account kato will impersonate and (b) an API token for that account. Then `KATO_<PLATFORM>_ASSIGNEE` is the login of that account; kato only picks up tickets assigned to it.

**Agent backend.** Use `claude` if you have a Claude Max/Pro subscription or an Anthropic API key — it's faster to set up and runs without Docker. Use `openhands` if you want self-hosted LLM control (Bedrock, OpenRouter, OpenAI compatible) or you're already standardized on OpenHands.

**Repository root.** Set `REPOSITORY_ROOT_PATH` to a folder that contains the repos you want kato to touch. Each subfolder must already be `git clone`d. kato will not clone repos for you on first run — it expects them present and on a clean branch.

---

## 7. Common issues

**`make bootstrap` fails on `pip install`.** You're probably on Python 3.10 or older. Upgrade to 3.11+.

**`make doctor` says `KATO_AGENT_BACKEND=claude is not supported inside Docker`.** The Claude CLI authenticates against host credentials (Keychain / config file) that don't survive into a container. Either run kato locally with `make run`, or switch to `KATO_AGENT_BACKEND=openhands` and use `make compose-up-docker`.

**Planning UI shows `Connecting to kato…` forever.** The Flask thread never started. Check the kato terminal for a webserver error. `KATO_WEBSERVER_DISABLED=1` will hide this — make sure it's unset.

**Scan tick keeps logging `task scan failed; retrying in 60 seconds`.** Almost always a credentials problem on the issue platform. Run `make doctor` and check the token + assignee for the active platform.

**`KATO_CLAUDE_BYPASS_PERMISSIONS=true` but kato refuses to start.** Working as designed. Bypass mode requires interactive confirmation on a TTY at startup — there is no flag-only escape hatch. Either run `make compose-up` from a real terminal (you'll be double-prompted to confirm), or unset the flag. Refused under root regardless. Also requires Docker — see [BYPASS_PROTECTIONS.md](BYPASS_PROTECTIONS.md).

**`make compose-up` opens a browser tab but nothing happens.** No tasks are assigned to your `*_ASSIGNEE`. Assign one and wait one scan cycle.

---

## 8. What to read next

- [README.md](README.md) — full reference (every env var, every flow, every troubleshooting case).
- [SECURITY.md](SECURITY.md) — threat model + operator responsibilities. Required reading before enabling `KATO_CLAUDE_BYPASS_PERMISSIONS=true`.
- [architecture.md](architecture.md) — code map: which module owns what, how the pieces compose at boot.
- [AGENTS.md](AGENTS.md) — coding rules for contributors (and for kato when it works on its own codebase).

---

## 9. One-shot reset

If you want to start over completely:

**macOS / Linux:**

```bash
rm -rf .venv .env webserver/static/build
rm -rf ~/.kato/workspaces ~/.kato/sessions     # wipe all task state
rm -rf ~/.claude/projects                       # wipe Claude transcripts (optional)
make bootstrap
make configure
make run
```

**Windows (PowerShell):**

```powershell
Remove-Item -Recurse -Force .venv, .env, webserver\static\build -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force $env:USERPROFILE\.kato\workspaces, $env:USERPROFILE\.kato\sessions -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force $env:USERPROFILE\.claude\projects -ErrorAction SilentlyContinue  # optional
python scripts\bootstrap.py
python -m kato_core_lib.configure_project --output .env
python scripts\run_local.py
```

The Claude transcripts directory is **optional** to wipe — leaving it preserves history-replay for tabs that come back to old task ids.

Workspace folders (`~/.kato/workspaces/<task-id>/` on POSIX, `%USERPROFILE%\.kato\workspaces\<task-id>\` on Windows) are per-task clones — wiping them only affects in-flight work. The repos under `REPOSITORY_ROOT_PATH` are untouched.

---

## 10. Cross-platform notes

Every operator-facing entry point is a Python script under `scripts/`; the `.sh` files in that folder are thin POSIX wrappers that delegate to the same Python code. On Windows you can call the Python scripts directly with no Bash / WSL dependency:

| Action | macOS / Linux | Windows |
|---|---|---|
| Bootstrap | `make bootstrap` | `python scripts\bootstrap.py` |
| Configure `.env` | `make configure` | `python -m kato_core_lib.configure_project --output .env` |
| Validate config | `make doctor` | `python -m kato_core_lib.validate_env --env-file .env --mode all` |
| Run kato | `make run` / `make compose-up` | `python scripts\run_local.py` |
| Run the test suite | `make test` | `python -m unittest discover -s tests` |
| Clean Docker resources | `./clean.sh` | `python scripts\clean.py` |

`make` is convenient on POSIX systems but adds nothing the Python scripts don't already do. Don't install Make on Windows just for kato — call the Python scripts directly.
