# Kato Desktop 0.2.0

Kato now reviews its own work before you do, takes tasks that live in no tracker at all, and opens pull requests on **Azure DevOps**.

## New

- **Review loop** — an independent reviewer reads the whole change and posts what it finds into the task's chat. The chat fixes each finding (with a regression test) or rejects it with evidence, and the next review is told those decisions. It repeats until a review comes back clean or the round limit is reached. Optional stages: a self-check in the main chat first, tests that must pass before "clean", and clean-room sweeps that must agree. Pick the reviewer's model and up to 30 rounds in the loop view.
- **Resume a review loop** — a loop that was stopped, failed, interrupted by a restart, or got stuck picks up where it left off — same rounds, same decisions — instead of starting over at round 1. A chat that was cut off mid-fix gets a "continue" nudge, not the findings again.
- **Tasks without a tracker** — a "New task" tab: write the summary and description, pick the repositories, and start it in plan, implement or chat mode.
- **Azure DevOps (Azure Repos)** — a git host alongside GitHub, GitLab and Bitbucket: push, open pull requests, and handle review comments on `dev.azure.com`, legacy `*.visualstudio.com` and on-prem Azure DevOps Server. Set it up in **Settings → Git provider → Azure DevOps** with a personal access token (Code: Read & write) and the bot account's sign-in name. Azure Boards is not a task provider yet.
- **Claude and Codex side by side** — a task holds one chat per agent, with a tab strip to switch between them.
- **Ticket screenshots in every chat** — images attached to a ticket are downloaded into the task and handed to the agent, now including chats started with `kato:wait-planning` / `kato:wait-editing`.
- **Retry a safeguard-flagged turn** — when Claude's safeguards refuse a turn, one click retries it on a pinned fallback model (`KATO_CLAUDE_FALLBACK_MODEL`).
- **Plan approval** — when the agent finishes planning, its plan opens in its own approval dialog. File search gains **match-case / exact-match** toggles.
- **PR comment polling switch** — turn review-comment handling off entirely from Settings, applied immediately.

## Changed

- **Kato never pushes on its own.** Every task waits for your Push after testing, tagged or not. Set `KATO_AUTO_PUSH_ENABLED=true` to let it publish autonomously.
- **Pull-request comments must @-mention kato** to be acted on (on by default — `KATO_REVIEW_COMMENTS_REQUIRE_MENTION`). Reviewer-to-reviewer chatter is left alone.
- **The Docker sandbox requires gVisor** (`runsc`), with no override. Docker Desktop cannot run gVisor — use a Linux host, or a Lima/Colima VM with `runsc` registered. The sandbox stays optional; without it nothing changes.

## Fixes and speed

- An empty repository on the git host now says "push a first commit" instead of failing with a misleading clone error.
- Faster repository sync, task delete and repository scan; many UI, Windows and stability fixes.

## Install

Grab the file for your OS from the Assets below. **Kato 0.1.0 updates itself** — it checks for the new release, verifies its signature, and installs it on restart.

**macOS** (`Kato_0.2.0_aarch64.dmg`, Apple Silicon)
Open the `.dmg` and drag **Kato** to Applications. This build isn't Apple-notarized yet, so on first launch macOS may block it. Clear the quarantine flag once:

```bash
xattr -cr /Applications/Kato.app
```

Then open Kato normally.

**Windows** (`.exe` installer) — run it and follow the prompts. Windows SmartScreen may warn on an unsigned installer; choose **More info → Run anyway**.

**Linux** (`.AppImage`) — `chmod +x Kato_0.2.0_amd64.AppImage` then run it.

## Requirements

- **`git`**
- An **agent CLI** — Claude Code (`claude`) or Codex
- **Docker with gVisor** — only for the optional hardened sandbox

## Notes

- **Apple Silicon only** on macOS this release; Intel/universal builds to follow.
- macOS/Windows binaries aren't notarized/signed yet — hence the one-time Gatekeeper/SmartScreen step above.

---

🤖 Built and released with [Claude Code](https://claude.com/claude-code)
