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

## 5-minute start

```bash
git clone <this-repo>
cd kato

kato bootstrap     # one-time: Python venv + dependencies
kato configure     # interactive wizard for your .env (ticket platform, repos, LLM)
kato doctor        # checks your config is valid
kato up            # starts kato + opens the planning UI in your browser
```

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
