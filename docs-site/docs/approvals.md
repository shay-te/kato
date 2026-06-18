---
sidebar_position: 4
title: Approvals
---

# Approvals — one popup, full control

Running an agent usually means babysitting a terminal that prompts for
permission. Kato collapses that into **one approval surface** for everything,
across every task.

## The central approval popup

When the agent wants to do something that needs permission, Kato shows a single
modal with:

- **The exact command or tool** it wants to run (re-derived server-side — Kato
  never trusts the client to report what's running).
- **Why it's risky**, when Action Guard flags it (the risk category + reason).
- Your choices: **Allow**, **Allow always** (remember this decision), or
  **Deny** — with an optional rationale the agent reads and adapts to.

The popup is **global**: a permission request from a task running in the
background still pops here, so you never switch windows to find a prompt that's
silently blocking work. If a live event is ever missed, Kato reconnects on a
short grace and surfaces it — no page refresh required.

## Allow always — and when it's withheld

For routine, repeatable actions you can pick **Allow always** and Kato remembers
it (keyed by the command's program signature, so a remembered `mvn` never
green-lights a `docker`). For genuinely high-risk categories (credential reads,
network exfiltration, remote-exec, sandbox escape) "Allow always" is **withheld**
— those always ask, every time.

## Repository approval

Kato only works in repos you've approved. Instead of trusting an invisible
config file, you approve repositories from a dedicated settings screen — so the
set of places the agent can write is something you can see and change.

## Nothing ships without you

Approvals are only half the story. Kato also **never** commits, pushes, opens a
PR, or resolves a review thread on its own. It makes the change, runs the tests,
and stops. You review the diff and click **Done — Push** when you're ready.

See [Security & Action Guard](./security) for what gets flagged and how to tune
it.
