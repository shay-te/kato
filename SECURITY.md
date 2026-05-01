# Security policy — kato

This file covers two things: **(1)** how to report a vulnerability, and **(2)** the threat model that operators should understand before running kato unattended. The shorter operator-facing summary lives in the README under "Security model — note" and "Operator responsibilities"; this file is the longer version.

---

## Reporting a vulnerability

If you believe you've found a security issue in kato — credential exposure, an escape from the per-task workspace, prompt-injection that bypasses the planning UI permission layer, anything else with a security shape — please report it privately rather than opening a public issue.

**Preferred channel:** open a [GitHub security advisory](https://github.com/shay-te/kato/security/advisories/new) on the repository. This routes the report to the maintainers without making it public.

**Fallback channel:** email the project maintainer listed in [`pyproject.toml`](pyproject.toml) / git history. Use a subject line that starts with `[kato security]` so it's not lost.

When reporting, please include:
- a description of the issue and the impact you're concerned about,
- a reproduction or proof-of-concept if one exists,
- the kato version (commit SHA) and configuration shape (issue platform, agent backend, whether `KATO_CLAUDE_BYPASS_PERMISSIONS` is on),
- any suggested mitigation if you have one.

We aim to acknowledge reports within a reasonable window and discuss disclosure timing with the reporter. Kato is a single-maintainer open-source project; please be patient.

---

## Threat model

Kato is an **agent orchestrator**, not a sandbox. The threat model below is what operators should assume when deciding how and where to run kato.

### What kato is trusted to do, by design

Kato runs with the operator's credentials and acts on the operator's behalf:

- It reads task descriptions and comments from the configured ticket platform (YouTrack / Jira / GitHub / GitLab / Bitbucket) using a token the operator supplied.
- It runs the agent backend — Claude Code CLI (`claude -p` / streaming) or OpenHands — as a subprocess with **the same OS-level privileges as the kato process itself**.
- It clones, edits, commits, and pushes git repositories the operator pointed it at, using whatever git credentials the operator made available (SSH agent, HTTPS token, etc.).
- It opens pull requests and writes comments back to the ticket platform.

There is **no internal privilege boundary** between kato and the agent it spawns. If kato can reach a system, the agent can too.

### What is in-scope for the threat model

Kato treats the **task description, ticket comments, repository contents, and agent output as untrusted input**. The relevant attacker classes:

1. **A malicious or compromised agent / model.** The agent could ignore the prompt-level guardrails, attempt to read files outside the workspace, exfiltrate environment variables, attempt to push to unintended remotes, or write malicious code that passes review.
2. **Prompt injection via task content.** A ticket description, code comment, file in the repository, or PR review comment can contain instructions the agent obeys. Anyone who can write to your ticket platform or repository can attempt this.
3. **A compromised dependency in the kato process tree.** Kato pulls in `core-lib`, `email-core-lib`, `hydra-core`, `omegaconf`, `jinja2`, plus whatever the agent backend pulls in. A supply-chain attack on any of these reaches the kato process.
4. **An operator who flips the wrong flag.** Specifically `KATO_CLAUDE_BYPASS_PERMISSIONS=true`, but the same applies to passing over-broad credentials, pointing kato at unowned systems, or running it as root.

### What is currently mitigated, and how strongly

| Risk | Current mitigation | Strength |
|---|---|---|
| Agent runs unbounded tools without operator awareness | Per-tool permission prompts via the planning UI when `KATO_CLAUDE_BYPASS_PERMISSIONS=false` (default). Each Bash/Edit/Write call requires manual Approve/Deny. | **Strong, but only when the operator is watching.** Removed entirely when the bypass flag is on. |
| Agent escapes the task's repository on disk | Per-task workspace clones under `~/.kato/workspaces/<task-id>/<repo>/`. Kato never `cd`s the agent into anything outside that path. | **Soft.** It's a working-directory convention, not a chroot. The agent can still read any file the kato process can read. |
| Two parallel tasks corrupt each other | Per-task workspace folder + branch state isolation. | **Strong** for the filesystem/branch axis; the agent backend's own state may still be shared. |
| Agent pushes to an unexpected remote | Branch publishability validation runs before push; remotes are derived from the cloned repo's git config, not from the agent. | **Medium.** Stops the obvious case; doesn't stop an agent that rewrites `.git/config` first. |
| Agent uses kato's network access for unintended traffic | None today. The agent has the same network access as the kato process. | **None at the kato layer.** |
| Agent reads filesystem outside the workspace | None today. The agent has the same filesystem access as the kato process. | **None at the kato layer.** |
| Prompt-injection via task content | Prompt-level guardrails ([cli_client.py](kato/client/claude/cli_client.py)) ask the agent not to follow embedded instructions, plus the per-tool permission layer above. | **Weak / advisory.** A determined injection can still steer the agent; the permission layer is what actually catches it. |
| Operator enables `KATO_CLAUDE_BYPASS_PERMISSIONS` without realizing | Flag ships `false` in `.env.example`. Kato logs a `WARNING` on every Claude spawn when it's `true`. README "Operator responsibilities" section. Refused when the kato process runs as root. | **Documentation + runtime warning.** Cannot prevent a deliberate operator decision. |
| Compromised dependency | Pinned `requires-python>=3.11`, version floors in `pyproject.toml`. Otherwise relies on operator-side scanning (Dependabot, etc.). | **Operator-managed.** |

### What is explicitly **not** mitigated

The following protections do not exist in kato today. Operators who need them must build them at a layer outside kato.

- **Network isolation for the agent.** The agent has the same network reach as the kato process — DNS, outbound HTTP(S), SSH to git remotes, anything else.
- **Filesystem sandboxing.** The agent can read anything the kato process can read. That includes `~/.ssh/`, `~/.aws/`, `~/.npmrc`, `.env` files outside the workspace, and any file the operator's user has access to.
- **Per-task containerization.** Each task is isolated by *folder*, not by *container*. A successful escape from the working-directory convention reaches the host.
- **Secret-scope reduction.** Tokens passed via env vars (YOUTRACK_TOKEN, GITHUB_API_TOKEN, JIRA_TOKEN, ANTHROPIC_API_KEY, CLAUDE_CODE_OAUTH_TOKEN, etc.) are visible to the agent backend's environment. There is no token-vending layer that hands the agent narrower-scope credentials.
- **Audit trail beyond stdout/stderr.** Kato logs to its configured logger. There is no tamper-resistant audit log.
- **Compliance properties.** Kato makes no claims of SOC 2, HIPAA, GDPR, ISO 27001, FedRAMP, or any other compliance posture. If your use case requires one, you must add the controls yourself.

### Recommended sandbox layers

If you are running kato unattended (cron, server, CI, or with `KATO_CLAUDE_BYPASS_PERMISSIONS=true`), pick at least one of these:

1. **[Claude Code's devcontainer](https://code.claude.com/docs/en/devcontainer)** — run the `claude` binary inside a network-restricted container with only the per-task workspace mounted in. Kato does not wire this automatically yet, but you can point `KATO_CLAUDE_BINARY` at a wrapper script that launches `claude` inside the devcontainer. Recommended for unattended runs.
2. **Dedicated VM or jail.** Run kato as an unprivileged user inside a VM whose only persistent state is the workspace + the credentials kato needs. Snapshot/reset between runs.
3. **Scoped credentials.** Where the platform supports it (GitHub fine-grained PATs, GitLab project tokens, Jira API tokens with limited scope), give kato a token whose blast radius matches the repos and projects you actually want it to touch — not a personal admin token.
4. **Egress firewall.** Allowlist outbound traffic to the ticket platform, the git remote, and the agent backend (api.anthropic.com / your OpenHands endpoint). Block everything else.

The `core-lib` framework that kato is built on does not provide any of these layers either; this is purely an operator-side concern.

### Out of scope

The following are explicitly **not** kato security concerns and won't be treated as vulnerabilities:

- **The agent doing what an operator told it to do.** If you opened a task that says "delete the database" and the agent does, that is operator error.
- **The MIT no-warranty disclaimer.** Kato ships under MIT and the disclaimer is intentional. See [LICENSE](LICENSE) and the README.
- **Loss of work caused by routine git operations.** Kato pushes branches and opens PRs; it does not force-push to protected branches or rewrite history.
- **Costs incurred by the agent backend.** Token spend caused by long tasks, retries, or `KATO_CLAUDE_MAX_TURNS` being unset is an operator concern.
- **Vulnerabilities in upstream packages** — `core-lib`, `claude` CLI, OpenHands, etc. — should be reported to the respective upstream projects.

---

## Disclosure history

No advisories published yet. This section will list resolved advisories with affected version ranges as they accumulate.
