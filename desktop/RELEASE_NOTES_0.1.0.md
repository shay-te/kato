# Kato Desktop 0.1.0

The first release of the **Kato desktop app** — one native window for running your coding agents on real tickets, with every approval in one place and git access gated behind your click.

Kato polls your tracker (YouTrack / Jira / Bitbucket / GitHub / GitLab), clones each task into an isolated workspace, runs an agent (Claude / Codex) to implement the fix, and opens a pull request — only ever pushing when **you** approve.

## Highlights

- **One screen for every task** — all your tickets across all your projects in a single tabbed UI, a fleet of agents working in parallel.
- **One approval popup** — every agent's permission request surfaces in a single global window, so nothing stalls waiting on you and nothing runs behind your back.
- **See what's approved** — one place that shows what's been allowed, blocked, and what's waiting.
- **Git only through Kato** — the agent's direct git access is blocked on every spawn; branches, pushes, and PRs happen through Kato, so nothing reaches your remote without your click. No auto-commit, no auto-push, no auto-resolve.
- **Action Guard** — credential reads, network exfiltration, remote exec, and sandbox escape are blocked; destructive/privileged actions require approval.
- **Bundled runtime** — ships the Python runtime; no `pip`/venv setup. Shares the same `~/.kato` state as the `kato` CLI, so nothing is lost switching between them.
- **Signed auto-update** — the app checks for new releases, verifies the signature, and updates on restart (the VS Code model).

## Requirements

Kato orchestrates tools that must already be on your machine:

- **`git`**
- **Docker** (for the hardened sandbox)
- An **agent CLI** — Claude Code (`claude`) or Codex

## Install

Grab the file for your OS from the Assets below.

**macOS** (`Kato_0.1.0_aarch64.dmg`, Apple Silicon)
Open the `.dmg` and drag **Kato** to Applications. This build isn't Apple-notarized yet, so on first launch macOS may block it. Clear the quarantine flag once:

```bash
xattr -cr /Applications/Kato.app
```

Then open Kato normally.

**Windows** (`.exe` installer) — run it and follow the prompts. Windows SmartScreen may warn on an unsigned installer; choose **More info → Run anyway**.

**Linux** (`.AppImage`) — `chmod +x Kato_0.1.0_amd64.AppImage` then run it.

## First run

On first launch Kato opens a setup wizard in the app to connect your tracker, repositories, and agent CLI — no terminal needed. Point it at your repos, and it starts picking up assigned tasks.

## Notes

- **Apple Silicon only** on macOS this release; Intel/universal builds to follow.
- macOS/Windows binaries aren't notarized/signed yet — hence the one-time Gatekeeper/SmartScreen step above. Code signing + notarization are planned.

---

🤖 Built and released with [Claude Code](https://claude.com/claude-code)
