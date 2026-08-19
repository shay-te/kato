<p align="center">
  <img src="./kato.png" alt="Kato" width="220" />
</p>

# Kato

<p align="center">
  <img src="./docs/img/bruce-lee-kato.jpg" alt="Bruce Lee as Kato in The Green Hornet (1966)" width="180" />
  <br />
  <em>Kato will help you kick all your tasks.</em>
</p>

**Kato is your autonomous coding sidekick.** Assign it a ticket in YouTrack, Jira, GitHub, GitLab, or Bitbucket — kato clones the repo, writes the code with Claude (or OpenHands), runs your tests, opens a pull request, and posts a summary back on the ticket. If reviewers leave PR comments, kato either fixes them or answers in the thread.

You stay in control: review every diff before merging, chat with the agent live through the built-in planning UI, or pause kato before it pushes anything.

---

## Why Use Kato

> **Ship more tickets with an AI agent you can actually trust — from one screen, with one approval popup, behind security you control.**

Coding agents are powerful but scary: they run shell commands, touch credentials, and sprawl across terminals and tabs. Kato turns that chaos into a **single, governed cockpit** — so a team can put an agent on real tickets without losing sleep over what it might do.

### Do more, in one place
- **All your tasks in a single pane.** Every ticket the agent is working — across every repo — lives in one tabbed UI. Stop hopping between terminals, IDEs, and browser tabs.
- **One agent for every tracker & git host.** YouTrack, Jira, GitHub, GitLab, Bitbucket — kato picks up assigned tickets and opens PRs wherever they belong.
- **Run on the agent you like.** Claude or OpenHands behind the same UI; pick the model and effort per task. (A Codex transport exists in the tree but is not yet a selectable backend — see `codex_core_lib`.)
- **It handles the busywork end to end** — clone, branch, code, test, open the PR, post a summary, and fix or answer review comments in-thread.

### Approve everything from one popup — never window-hop again
- **Central approval popup.** Every permission request — even from a backgrounded task — surfaces in one modal. No more hunting across windows for the prompt that's blocking work.
- **Full control on every prompt.** See the exact command/tool and choose Allow, Allow-always, or Deny (with a reason the agent reads and adapts to).
- **One screen to approve repositories** kato may touch — instead of trusting a config file you can't see.
- **Nothing ships without you.** No auto-commit, no auto-push, no auto-resolve. Kato makes the change, runs the tests, and stops — you click **Done — Push** when it's ready.

### Security that's visible, not hand-wavy
- **Action Guard.** A 3-layer guard blocks the antivirus-tripping patterns — credential reads, network exfiltration, remote-exec, sandbox escape — and asks before dual-use actions (`rm`, `chmod`, writes outside the task folder). Tune Block / Ask / Allow per category from the UI; a no-legit-use floor can never be loosened.
- **Out-of-folder writes always ask.** Even scratch paths like `/tmp` are routed back through your approval instead of being silently auto-accepted.
- **Isolated per-task workspaces.** Each task runs in its own sandboxed clone; in-review clones are never auto-deleted, and kato never deletes a task or workspace on its own.
- **Tamper-evident audit log.** Every guarded decision is recorded (hash-chained, with redacted command digests) so you can prove exactly what ran.

### See and steer the work
- **Live diff viewer + inline comments.** Read every change as it lands and drop a comment on any line — kato treats it as a new instruction and re-runs.
- **Smart PR-comment handling.** Reviewer comments get fixed or answered in-thread; comments that @-mention a *human* (not the bot) are ignored, so kato never acts on a side conversation.
- **Gets better over time.** Lessons from past tasks are fed back into future runs.

**The bottom line:** Kato gives a team the throughput of an autonomous agent with the oversight of a senior reviewer — one cockpit, one approval flow, security you can see. **[Get started in 5 minutes ↓](#5-minute-start)**

---

## 5-minute start

```bash
git clone <this-repo>
cd kato

./kato up          # that's it — first run bootstraps itself (venv + deps),
                   # then opens the setup wizard in your browser
                   # (config lives in ~/.kato/settings.json)
```

Useful extras: `kato doctor` validates your config; `kato bootstrap` re-runs
the full environment setup (incl. the test suite) explicitly.

That's it. To make kato work a ticket: open it in your tracker, **assign it to yourself**, and add the tag `kato:repo:<repo-folder-name>` (e.g. `kato:repo:my-backend`). Kato picks it up on the next 30-second scan tick.

> Want to chat with the agent instead of letting it run on its own? Add the tag `kato:wait-planning` — kato opens a chat tab for the ticket and waits for you to drive the conversation.

---

## What kato can do for you

- 🎫 **Watch your tickets** — YouTrack, Jira, GitHub Issues, GitLab Issues, or Bitbucket Issues
- 🤖 **Pick your agent** — Claude Code CLI (local) or OpenHands (HTTP), switchable with one env var
- 🌿 **Isolate every task** — fresh clone per ticket under `~/.kato/workspaces/<ticket-id>/`
- 🧪 **Run your tests** — optional dedicated testing container, or skip testing entirely
- 📬 **Open pull requests** — one PR per repo, summary auto-posted back on the ticket
- 💬 **Handle reviewer feedback** — fix the comment OR reply in the thread, kato decides
- 🔐 **Block bad work before it starts** — `.env` / secret / CVE scanner runs before the agent sees the code
- 🛡 **Block harmful actions while it runs** — Action Guard refuses credential theft, network exfiltration, and destructive commands; you set Block / Ask / Allow per category
- 🖥 **Watch it work live** — Planning UI (Flask + React) with chat, file tree, diffs, status bar
- ⏸ **Pause before push** — tag `kato:wait-before-git-push` and approve PR creation from the UI
- 📂 **Multi-repo tickets** — one ticket → one PR per `kato:repo:<name>` tag
- 🔔 **Notifications** — email + Slack on completion / failure

---

## Documentation map

Pick the page that matches what you're trying to do:

| If you want to… | Read |
|---|---|
| Understand what tags control kato behavior | [readmeTags.md](readmeTags.md) |
| Connect kato to your ticket tracker | [readmeIssuePlatforms.md](readmeIssuePlatforms.md) |
| Switch from OpenHands to Claude Code (or vice-versa) | [readmeAgentBackend.md](readmeAgentBackend.md) |
| Configure OpenHands with Bedrock or OpenRouter | [readmeOpenHands.md](readmeOpenHands.md) |
| See every env var kato reads | [readmeEnvironmentReference.md](readmeEnvironmentReference.md) |
| Walk through full setup / Docker Compose / manual flow | [readmeHowToUse.md](readmeHowToUse.md) — or the shorter [SETUP.md](SETUP.md) |
| Rebuild the Planning UI / clean stuck state | [readmePlanningUI.md](readmePlanningUI.md) |
| Understand the security model + your responsibilities | [readmeSecurity.md](readmeSecurity.md) |
| Run the test suite | [readmeTesting.md](readmeTesting.md) |
| Debug a problem or cut LLM cost | [readmeTroubleshooting.md](readmeTroubleshooting.md) |
| See how kato is built (architecture, flows, layering) | [readmeArchitecture.md](readmeArchitecture.md) — or the deeper [architecture.md](architecture.md) |
| Report a vulnerability / read the threat model | [SECURITY.md](SECURITY.md) |
| Read the bypass-permissions defense layers | [BYPASS_PROTECTIONS.md](BYPASS_PROTECTIONS.md) |
| Contribute code | [CONTRIBUTING.md](CONTRIBUTING.md) |
| Give the agent project-specific coding rules | [AGENTS.md](AGENTS.md) |
| Adopt an existing Claude session | [ADOPTING_EXISTING_CLAUDE_SESSIONS.md](ADOPTING_EXISTING_CLAUDE_SESSIONS.md) |

---

## How Kato protects you

Kato runs an autonomous agent on your code, your repos, and your credentials — so it's built **defense-in-depth**: independent layers, each of which would have to fail for the agent to do real harm. Most are on by default; none of them replace the final human review.

**Before the agent runs**

- 🚫 **Repository approval (Restricted Execution Protocol)** — kato refuses to act on any repo you haven't explicitly approved with `kato approve-repo`, and never touches one on the **repository denylist**.
- 🔍 **Pre-execution security scanner** — `detect-secrets`, `bandit`, `safety`, `npm audit`, and a committed-`.env` checker run against the fresh clone; CRITICAL/HIGH findings block the task *before the agent sees the code*.
- 🌿 **Isolated workspace per task** — a fresh clone under `~/.kato/workspaces/<ticket-id>/`; one task can't reach another's files.

**While the agent runs — Action Guard**

- 🛡 **Blocks harmful actions** — every tool call is classified for risk: destructive filesystem ops, **credential reads** (`~/.ssh`, `~/.aws`, private keys), **network exfiltration**, `curl … | sh`, persistence/backdoors (cron, `authorized_keys`), privilege escalation, sandbox escape, and out-of-workspace paths. No-legitimate-use actions (reverse shells, fork bombs, `mkfs`, `dd`-to-device) are refused at the Claude CLI itself — in **every** mode.
- 🎛 **You set the posture** — per-category **Block / Ask / Allow** in *Settings → Action Guard* (live, no restart). Secure defaults: credential reads, exfiltration, and remote-exec **block** outright; dual-use ops **ask** you first. The catastrophic floor can't be loosened.
- 🧾 **Tamper-evident audit** — every block and approval is hash-chained to `~/.kato/action-guard-audit.log` (records a command digest, never your secrets). Boot and `kato doctor` print the active posture.

**Always on**

- ✋ **Per-tool Approve / Deny** — the agent's tool calls go through a permission modal in the Planning UI by default; high-risk ones can never be "allowed always".
- 🔒 **Hard git denylist** — the agent can never `push`, `commit`, `reset`, or otherwise mutate git. Kato owns the branch and the PR; nothing leaves your machine until you click **Done – Push**.

**Optional hardening (one env var)**

- 🐳 **Docker sandbox** (`KATO_CLAUDE_DOCKER=true`) — runs every agent in a hardened container: gVisor isolation, all Linux capabilities dropped, read-only root filesystem, and a default-DROP egress firewall that allows only `api.anthropic.com`. Turning prompts off (`KATO_CLAUDE_BYPASS_PERMISSIONS=true`) **requires** this sandbox and refuses to start under root, under CI/cron, or without an interactive double-confirm — see [BYPASS_PROTECTIONS.md](BYPASS_PROTECTIONS.md).

**The real safety net:** all of the above buys you time and stops the obvious attacks, but the guarantee is the same one you use for human contributors — **review every diff before you merge.** Kato never pushes or merges on its own. Full model in [readmeSecurity.md](readmeSecurity.md) and [SECURITY.md](SECURITY.md).

---

## Why "Kato"?

The name comes from Kato, the Green Hornet's sidekick, famously played by Bruce Lee. That makes it a fitting name for this project: a helper that works alongside the main mission, stays useful in the background, and helps get important work done.

I love and respect Bruce Lee, and I wanted the name to reflect that admiration.

---

## License

MIT — no warranty. You run kato on your code, your repos, and your credentials at your own risk. The maintainers do not take responsibility for damage caused by the agent. If your use case needs guaranteed isolation or compliance (SOC 2, HIPAA, GDPR, export control), build that layer yourself before pointing kato at production work. See [LICENSE](LICENSE) and the longer note in [readmeSecurity.md](readmeSecurity.md).
