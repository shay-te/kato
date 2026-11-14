# Kato Desktop 0.3.0

Kato now splits big work into sub-tasks that wait for each other, hands a task over to a teammate's kato, and keeps every task in one rail on the left. The review loop got stricter about what it reviews and easier to follow.

## New

- **Tasks rail** — every task is a row on the left edge, always there: collapsed to its status dot and code, expanded on hover, pinned open (and resizable) with a click on the kato logo. Sub-tasks sit under their main task, and a group folds. **Tab / Ctrl+Tab** switch tasks; hold the key to see them all.
- **Split a plan into sub-tasks** — the plan tab splits a task's plan (or a split you wrote as files) into small tasks that wait for each other, and releases each one only once the tasks before it are merged. Tell the agent how you want it split before it starts ("at most 4 tasks, one per repository"). On YouTrack and Jira the sub-tasks can be real tickets, so a teammate's kato follows the same plan. Every sub-task's pull request goes into the main task's branch, which carries the whole plan in one PR.
- **Hand over a task** — the header's **Hand over** gives a task to someone else's kato (YouTrack / Jira): assigns it, clears kato's blocking comments, moves it back to Open, and stops your agent. Choose whether their kato waits for them or starts by itself.
- **Grok and OpenCode** — chat tabs for xAI's Grok Build and OpenCode (any provider) beside Claude and Codex.
- **Strict git switch** (Settings → Strict git, on by default) — off, the agent may commit, merge and rebase on its own branch. Pushing always stays yours.
- **Automation settings** — one switch per thing kato does on its own: the security scan, running tests, stopping a task's containers on Done, release notes, emails.
- **Keep running with the lid closed** — on a charger (or on battery while an agent works), with a watchdog that restores your sleep settings if kato stops.
- **Usage** — tokens and an estimated cost per task, per model, including deleted tasks.
- **Release notes and Slack on Done** — a dated line per finished task in a notes file (optionally copied to a Google Doc), and a signed post in a Slack channel.
- **Standing instructions** — your own text added to every task's prompt (Settings → Prompts).
- **Commit and PR text that follows the repository's own CLAUDE.md / AGENTS.md** (opt-in).
- **Ticket tags** — `kato:review-loop[:<rounds>]` runs the review loop when the task is ready; `kato:model:<family>[:<version>]` picks its model; `kato:branch:<name>` names its branch; `kato:autopilot` presses Done when the loop comes back clean; `kato:auto-wait` does all of it and waits for your Done.
- **Rename a task's branch** — locally and on the remote, refused while a pull request is open.
- **Back after a restart** — agents that were working when kato stopped are resumed when it starts again. A **Shut down** button in the rail.
- **Files when creating a task** — attach, paste or drop documents into "New task"; the chat takes any file too.

## Changed

- **Review loop** — tests must pass before every review; reviews are held to the task and its plan; a re-check reads only what changed since the last review; a finding on the last round is still sent to be fixed. Tests that cannot run here, or already failed before the task, are listed apart and never block it. Start and Resume show at once.
- **No more repository approvals** — every repository under your folder is ready to work in. Keep kato out of one with the ignored-folders list or the denylist (Settings → Repositories).
- **No top bar** — everything it held moved into the rail; the review loop and split-plan buttons are in the task toolbar.
- **Deleting a task warns first** when its work was never pushed. A task's Docker containers are stopped on Done and removed when the task is deleted.
- **Edits inside the task folder** are approved by kato itself in "Edit automatically" mode, so a task with no one watching no longer stops at its first file.
- Pick an older Claude model by version in Settings.

## Fixes

- Jira search uses Atlassian's new `/search/jql` endpoint (the old one was removed).
- Pull request lookups are cached and back off when the git host answers "too many requests".
- Ticket moves are read back after they are made; repositories behind an SSH alias find their git host.
- A finished background job no longer keeps a chat "working"; Ctrl+C always stops kato.
- Many UI, Windows and stability fixes.

## Install

Grab the file for your OS from the Assets below. **Kato 0.2.0 updates itself**: it checks for the new release, verifies its signature, and installs it on restart.

**macOS** (`Kato_0.3.0_aarch64.dmg`, Apple Silicon): open the `.dmg` and drag **Kato** to Applications. The app is signed and notarized by Apple, so it opens normally.

**Windows** (`.exe` installer): run it and follow the prompts. The installer isn't signed yet, so Windows SmartScreen may warn; choose **More info → Run anyway**.

**Linux** (`.AppImage`): `chmod +x Kato_0.3.0_amd64.AppImage`, then run it.

## Requirements

- **`git`**
- An **agent CLI**: Claude Code (`claude`) or Codex; Grok and OpenCode are optional
- **Docker with gVisor**, only for the optional hardened sandbox

## Notes

- **Apple Silicon only** on macOS this release; Intel and universal builds are still to come.
- Hand over and the Jira search change follow the trackers' documentation and have not been tried on every tracker setup yet: tell us if yours behaves differently.

---

🤖 Built and released with [Claude Code](https://claude.com/claude-code)
