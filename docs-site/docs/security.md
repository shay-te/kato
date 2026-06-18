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
| Destructive FS | **Ask** | `rm -rf`, `chmod` on in-scope paths (catastrophic targets like `/`, `~` stay a hard block) |
| Persistence / priv-esc | **Ask** | writes to `~/.bashrc`, crontab; `sudo`, `docker` |
| Out-of-scope writes | **Ask** | writing outside the task workspace |
| Network tools | **Ask** | WebFetch / WebSearch / MCP connectors (dual-use) |
| New capability | **Ask** | any tool Kato doesn't recognize as safe-local |

You tune each category (Block / Ask / Allow) from the **Action Guard** settings
panel — live, no restart. The **floor** categories and detections (reverse
shell, fork bomb, `mkfs`, dd-to-device) can **never** be loosened below Block.

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
