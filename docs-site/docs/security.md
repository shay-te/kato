---
sidebar_position: 5
title: Security & Action Guard
---

# Security & Action Guard

Kato was hardened after an operator's antivirus fired on a raw coding agent's
actions. The result is **Action Guard** — operator-controlled blocking of
harmful actions that holds in every run mode.

## Three layers

1. **CLI denylist floor (always on).** A non-overridable `--disallowedTools`
   floor for zero-legit-use programs (`mkfs`, namespace-escape, host-power). The
   CLI refuses these in every permission mode.
2. **Content-aware guard (the permission path).** Every tool call is classified
   into a risk category and a decision — **Block**, **Ask**, or **Allow** —
   resolved live per action. A Block is auto-denied (the agent is told why and
   adapts); an Ask surfaces the [approval popup](./approvals) with the risk
   shown.
3. **Sandbox (structural backstop).** Per-task isolated workspaces; optional
   Docker sandbox with an egress firewall.

## Risk categories

| Category | Default | Examples |
| --- | --- | --- |
| Credential read | **Block** | reading `~/.ssh`, `~/.aws`, `/etc/shadow`, Keychain |
| Network exfiltration | **Block** | reverse shells, `curl -d @file`, `scp`/`nc` to a host |
| Remote-exec | **Block (floor)** | `curl … \| sh`, `bash -c "$(curl …)"` |
| Sandbox escape | **Block (floor)** | `nsenter`, `unshare`, `chroot` |
| Destructive FS | **Ask** | `rm -rf`, `chmod` on in-scope paths (catastrophic targets like `/`, `~` stay a hard block); a whole-tree `git restore .` |
| Persistence / priv-esc | **Ask** | writes to `~/.bashrc`, crontab; `sudo`, `docker` |
| Out-of-scope writes | **Ask** | writing outside the task workspace |
| Network tools | **Ask** | WebFetch / WebSearch / MCP connectors (dual-use) |
| New capability | **Ask** | any tool Kato doesn't recognize as safe-local |

You tune each category (Block / Ask / Allow) from the **Action Guard** settings
panel — live, no restart. The **floor** categories and detections (reverse
shell, fork bomb, `mkfs`, dd-to-device) can **never** be loosened below Block.

## Git: the orchestrator owns the branch, you own the files

Kato drives the branch state machine — it prepares the branch, creates the
commit, and publishes. That, and only that, is what the agent is denied:
`git commit`, `push`, `pull`, `fetch`, `merge`, `rebase`, `reset`, `checkout`,
`switch`, `branch` (plus the plumbing that reaches the same capabilities —
`commit-tree`, `send-pack`, `update-ref` …) are hard-denied at layer 1, in
**every** permission mode. `config` is denied too: it is the hook/RCE surface,
not a branch concern.

**Everything else in git is available**, and that is deliberate. Denying every
verb that can write anything is a much broader rule than "kato owns the
branch", and it cost real work — an operator could not ask the agent to look
up a file's history, undo a change, restore a file deleted three commits ago,
set work aside, or find a lost commit. So `log`, `show`, `diff`, `blame`,
`status`, `restore`, `stash`, `apply` and `reflog` all pass, subject to the
normal approval prompt.

`git restore <path>` is deliberately allowed. Reverting a file to its committed
state is ordinary editing, not branch movement, and blocking it meant the agent
had to refuse when an operator said "just revert that file". `git restore` is
the only file-scoped member of the family — it cannot move `HEAD` or switch
branches — so allowing it can't race the orchestrator.

The destructive FORMS of those verbs are caught by argv rather than by
denying the verb: `git stash drop`/`clear`, `git reflog expire`/`delete`, and
`git apply --unsafe-paths` (the one apply form that can write outside the
worktree) each go to you for approval, while the ordinary forms pass.

A **whole-tree** revert (`git restore .`) is different and asks first: nothing
is committed until Kato publishes, so discarding every uncommitted change
destroys the entire task with no commit and no reflog entry to recover from.
Layer 2 tells the two apart by reading the pathspec — and the test is an
allowlist: a revert counts as scoped only if every pathspec is demonstrably a
plain narrow path. Git's magic-pathspec grammar (`:!x`, `:(exclude)x`,
`:(glob)**`, a bare `:`) all mean "the whole tree", so anything that is not
clearly narrow — magic, glob, traversal, built at runtime, or read from a file
Kato can't see — is treated as whole-tree and goes to you. Unknown means ask,
never allow.

Remembered approvals are keyed per git **subcommand**, so an "always allow" on
`git status` never covers `git restore`, and approving a scoped revert never
pre-approves the whole-tree one.

One exception, by design: in `bypassPermissions` there is no per-tool prompt,
so layer 2 never runs and there is nobody to ask. `git restore` is denied
outright in that mode rather than allowed unsupervised — the capability exists
in every attended mode, and withdraws where it cannot be reviewed.

## Out-of-folder writes always ask

A coding agent's "accept edits" mode auto-accepts file writes — including scratch
paths like `/tmp` — which would bypass the guard. Kato forces those writes back
through the approval flow, and a loud chat warning records any write outside the
task folder as a backstop.

## Audit trail

Every guarded decision is appended to a **hash-chained** audit log with a
redacted command digest (never the raw command, which could contain a secret),
so a tampered entry is detectable and you can prove what ran. The boot banner
and `kato doctor` print the current posture.

## Defaults are safe, control is yours

Out of the box Kato is **balanced-secure**: catastrophic and exfiltration
patterns are blocked, dual-use actions ask. Every choice is yours to tighten or
loosen (except the floor) — and you can see exactly what the posture is at any
time.
