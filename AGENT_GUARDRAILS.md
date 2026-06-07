# How Kato Protects You From Agent Mistakes

Kato runs an autonomous coding agent (Claude/Codex/OpenHands) against your
repos. This document describes the layers that keep a confused — or
adversarially-prompted — agent from doing damage, **and is honest about where
each layer's guarantee ends.**

The layers are defense-in-depth: most are *advisory* (warn the operator,
withhold a remembered grant) and one is *enforcing* (OS-level confinement).
Only the enforcing layer is a true boundary; the rest raise the bar and catch
the common and the careless cases.

---

## 1. Per-action permission prompts (advisory)

The agent is spawned with `--permission-mode acceptEdits
--permission-prompt-tool stdio`. Every tool call the CLI routes for approval
surfaces a modal: **Deny / Allow once / Allow always**, showing the full,
real command or file. Nothing the agent asks for runs without either a live
decision or a remembered one.

- **Remembered decisions are keyed by the command's _program_, not the task
  and not the verbatim line.** "Allow always" on `mvn` covers future `mvn`
  runs in any task — but never `docker`. Chaining a new program
  (`mvn … && rm -rf …`) re-prompts instead of riding the `mvn` grant.
- Decisions persist in the browser (`kato.toolDecisions.v1`) and are
  reviewable/clearable in **Settings → Permissions**.

## 2. Prompt for ANY task, wherever you are (advisory)

A permission ask on a *backgrounded* task is surfaced no matter which task
you're viewing (`GET /api/permissions/pending` + the global modal), titled
with the task code ("UNA-2742 wants permission"). You can't miss an approval
because you happened to be on another tab.

## 3. Out-of-sandbox warning (advisory)

Each session is scoped to its task workspace (the `cwd`, its sibling repos
under the task folder, and the spawn-time `--add-dir` set). When a tool call
reaches a filesystem path **outside** that scope, the modal turns loud red
("CLAUDE IS REACHING OUTSIDE THE TASK FOLDER"), names the path, and
**withholds "Allow always"** — an out-of-sandbox grant must never be one
click from permanent.

Two detectors (`claude_core_lib/.../helpers/sandbox_scope.py`):

- **Structured tools** (`Read`/`Edit`/`Write`/`NotebookEdit`): the
  `file_path`/`path` argument is classified exactly — absolute *or*
  relative-to-`cwd`, with `..` escapes resolved lexically.
- **Bash commands** (`grep`/`cat`/`python -c "open('…')"`/redirects): absolute
  home-tree paths (`/Users`, `/home`, `~`) and relative `..` climb-outs are
  found **anywhere in the command** — including buried inside quotes, JSON
  bodies, `$(…)` substitutions, and walls of text. It is hardened against
  static obfuscation (quote-splitting `/Use"rs"/dev`, backslash escaping,
  `$HOME`/`${HOME}` indirection).

**Exempt (never warns):** paths inside the task, the configured
`lessons.md`/`architecture.md` (the agent is *meant* to touch those), system
trees (`/usr`,`/etc`,…), URLs (`//host/…`), and glob/regex fragments
(`*/main/*`). This keeps `git`/`ls`/`mvn` from drowning you in false alarms.

### Limits of the warning layer (read this)

The Bash detector is a **static string scanner**. It **cannot** see a path
that only exists at runtime:

- `$VAR` indirection where the value isn't a literal `$HOME` (`cat $SECRET`).
- base64/hex-encoded or otherwise computed paths.
- a path fetched from the network, then read.
- projects living **outside** `/Users`/`/home` (absolute non-home escapes are
  intentionally not flagged, to avoid noise — structured-tool access to them
  still is).
- symlinks (classification is lexical; we don't `realpath`).

It also doesn't model **exfiltration** — reading an *in-task* secret and
`curl`-POSTing it out is not a path escape.

For these, see Layer 6.

## 4. The git denylist (enforcing, within the agent)

Kato is the only component that runs `git`. The agent is spawned with a
hard, non-overridable `--disallowedTools` git denylist, so it can't push,
reset, or rewrite history directly. Branch/commit/push/PR is done by kato's
own vetted code path.

## 5. False-success guards (correctness, not security)

The agent can't fake progress:

- **Task with no diff** → status `NO_CHANGES`; the task is **not** moved to
  "In Review" and no PR is opened.
- **Review answer** → the thread is never auto-resolved and the reply is
  always prefixed with a visible "no code changed" disclaimer.
- A queued comment can't be marked "addressed" by a *previous* turn's result
  (the busy-turn / stall checks gate dispatch).

## 6. Docker confinement — the real boundary (enforcing)

`KATO_CLAUDE_DOCKER=true` runs every agent spawn inside the hardened
`sandbox_core_lib` container. This is the only layer that makes "no input can
escape" actually true, because it confines at the OS level **regardless of
how the command is written**:

- the task workspace is bind-mounted and **nothing else** is on the
  filesystem (read-only root, tmpfs scratch);
- the network is default-DROP, allowing only `api.anthropic.com:443`;
- `--cap-drop ALL`, `--security-opt no-new-privileges`, gVisor runtime,
  memory/pid/cpu limits;
- the workspace is pre-scanned and refused if it contains committed secrets.

A `$VAR`/base64/runtime-computed path that defeats the Layer-3 scanner still
cannot read `~/.ssh` from inside the container — the file simply isn't there.

See `sandbox_core_lib/SANDBOX_PROTECTIONS.md` for the full threat model and
the locked-in flag invariants, and `BYPASS_PROTECTIONS.md` for the gated
escape hatches (`KATO_CLAUDE_BYPASS_PERMISSIONS`,
`KATO_CLAUDE_ALLOWED_READ_ONLY_TOOLS`), which require docker mode.

---

## TL;DR

| Concern | Layer | Guarantee |
|---|---|---|
| Accidental edit/command | Permission prompts (1,2) | You approve every action |
| Reaching another repo / `~/.ssh` (literal path) | Sandbox warning (3) | Loud red, no remembered grant |
| Obfuscated / runtime-computed path escape | Sandbox warning (3) | **Not caught** — use Layer 6 |
| Agent running `git` directly | Git denylist (4) | Blocked |
| Faked "done" / silent no-op | False-success guards (5) | Surfaced honestly |
| Any filesystem/network escape | Docker (6) | **Enforced at the OS** |

If you need a hard guarantee, run with `KATO_CLAUDE_DOCKER=true`. Without it,
Layers 1–5 are strong defense-in-depth that catch the honest mistakes and the
non-cryptographic attempts, but they are not a substitute for OS confinement.
