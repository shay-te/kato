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

Both paths run the same script. It creates `.venv/`, installs kato + the webserver in editable mode, builds the planning UI bundle if `npm` is available, and runs the test suite. Configuration happens afterwards in the browser: `kato up` opens the first-run setup wizard (everything is saved to `~/.kato/settings.json` — kato does not read `.env` files).

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
| Issue-platform credentials | `YOUTRACK_API_TOKEN` / `JIRA_API_TOKEN` / `GITHUB_API_TOKEN` / `GITLAB_API_TOKEN` / `BITBUCKET_API_TOKEN` — only the one for the platform you picked |
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

The first scan tick fires as soon as kato has finished starting up. After that kato scans every 60s.

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

### Windows: native or WSL2?

WSL (Windows Subsystem for Linux) is Microsoft's built-in way to run a real Linux distribution — Ubuntu, Debian — inside Windows. WSL2 runs an actual Linux kernel in a lightweight Hyper-V VM, so from kato's point of view a WSL2 distro simply *is* Linux.

Both paths work; pick by whether you want the Docker sandbox:

| | Native Windows | Inside WSL2 |
|---|---|---|
| `kato up`, the planning UI, git, the Claude/Codex CLI | Works | Works |
| `KATO_CLAUDE_DOCKER=true` (sandboxed agent) | **Refused** — the sandbox image is Linux, the workspace path validation assumes POSIX semantics, and `fcntl.flock` for the audit chain does not exist on Windows | Works, via Docker Desktop's WSL2 backend |
| `KATO_CLAUDE_BYPASS_PERMISSIONS=true` | Refused (it requires Docker) | Works |
| Sandbox layers | n/a | All of them as on Linux native, except gVisor — Docker Desktop cannot run it, so set `KATO_SANDBOX_ALLOW_NO_GVISOR=true`; the Hyper-V VM boundary is the substitute. See [SANDBOX_PROTECTIONS.md](sandbox_core_lib/SANDBOX_PROTECTIONS.md) |

Running under WSL2 needs no kato-specific configuration — install Python 3.11+, the agent CLI and kato *inside* the distro, then `kato up`. The UI is reachable from a Windows browser at the usual `127.0.0.1:5050`; WSL2 forwards localhost, so you get the planning UI in a normal Windows window with no GUI plumbing at all. This is the path to recommend.

**Which install, though?** The one that matters is: everything kato shells out to must live on the *same side* of the boundary. `git`, the agent CLI and the `docker` CLI are spawned as subprocesses from kato's own environment, so a Windows-side install of them is a different machine as far as kato is concerned.

- **Inside WSL2, headless (recommended):** `pip install -e .` + `kato up` in the distro, browser on the Windows side. No desktop app involved. The window is a real Windows window, so this is also the best way to *evaluate* kato on Windows — the desktop app adds a bundled Python runtime and auto-update, neither of which changes the product you are judging.
- **Inside WSL2, desktop app:** the **Linux** build (`.AppImage` / `.deb`) installed in the distro. It works, but it is a GTK/WebKit app rendered through WSLg (built into Windows 11; Windows 10 needs an X server), so it looks and behaves like a Linux window pasted onto Windows — not native. Prefer the headless option above.
- **The trap:** installing the Windows `.msi` on the Windows side and expecting it to drive a CLI installed in WSL. It cannot see it — and that is the native-Windows path that refuses `KATO_CLAUDE_DOCKER=true` (see the table above).

> **Also available: the desktop app's WSL target.** The Windows app can run its backend inside a WSL2 distribution while the window stays native — the VS Code Remote-WSL model. You never install or update kato inside the distro: the installer carries the Linux backend and pushes it in, version-keyed, so the app's own signed update covers both halves. On a fresh install it picks your default WSL2 distro automatically; an existing native install is left alone. Still yours to install *inside* the distro: `git`, the agent CLI, and Docker Desktop's WSL integration. See [desktop/README.md → Windows: the WSL target](desktop/README.md).

The desktop app bundles the Python runtime, but never `git`, Docker, or the agent CLI — those are always installed by the operator, on whichever side kato runs.

Two things to get right inside WSL2:

- Keep `REPOSITORY_ROOT_PATH` on the **Linux** filesystem (`~/projects`), not on `/mnt/c/...`. Cross-filesystem git is slow and drags in Windows permission/line-ending quirks.
- Docker Desktop must have WSL integration enabled for that distro (Settings → Resources → WSL integration), otherwise `docker` is not on the PATH kato sees.

CI runs kato's test suite on Linux only; native-Windows behavior is verified by hand (see [WINDOWS_VERIFICATION.md](WINDOWS_VERIFICATION.md)).
