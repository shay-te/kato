# Tag Reference — kato

Kato uses ticket-platform tags (YouTrack tags / Jira labels / GitHub labels / GitLab labels) namespaced under `kato:` to control per-task behavior. Apply or remove them on the ticket itself; kato reads them on every scan tick and reacts on the next pass.

| Tag | What it does |
|---|---|
| `kato:repo:<repo-name>` | **Required for any task that should produce a PR.** Names the repository folder (under `REPOSITORY_ROOT_PATH`) that kato should clone for this task. Add multiple tags to drive a multi-repo task — one PR per tag. The folder name must match the directory; case-sensitive. On a plan's sub-task the tag adds to the repositories the plan gave it. |
| `kato:wait-planning` | **Don't run autonomously — open a chat tab.** Kato registers the task in the planning UI and waits for the operator to chat with the agent. No implementation, no testing, no PR. Remove the tag to hand control back to the orchestrator. |
| `kato:wait-before-git-push` | **Run the agent, but pause before push + PR.** Kato runs implementation and testing as usual, commits to the local task branch, then stops. The operator approves the push via the planning UI's "Approve push" button (or by removing the tag and re-triggering the task). The push and PR creation are still done by kato — never by Claude. |
| `kato:review-loop` | **Review the task when it finishes.** Once a task kato ran by itself parks for push approval, its review loop starts on its own. Problems go back to the agent, and kato still doesn't push. `kato:review-loop:10` picks the rounds (1 to 30, default 5). No tag, no loop. Kato reads this tag when it picks up the ticket, and it does nothing on chats (`kato:wait-planning`, New task), which have no single finished moment. |
| `kato:branch:<name>` | **Name the branch.** The task works on `<name>` instead of the ticket id. It must be a new branch: kato leaves the tag out, works on the ticket id and says why in the task's preparation log if the branch already exists on the remote, is the one the repository merges into, belongs to another task, or the task has already started. Plan sub-tasks always use their ticket id. |
| `kato:triage:investigate` | **Classify the task instead of working it.** Kato spends one read-only Claude turn analyzing the task description and writes back exactly one `kato:triage:<level>` outcome tag (see below), then removes this tag. No code edits, no PR. Useful for triaging a backlog. |
| `kato:triage:critical` | Outcome: real, urgent. Set by the triage flow. |
| `kato:triage:high` | Outcome: real, work soon. |
| `kato:triage:medium` | Outcome: real, normal priority. |
| `kato:triage:low` | Outcome: real, low priority. |
| `kato:triage:duplicate` | Outcome: covered by another ticket. |
| `kato:triage:wontfix` | Outcome: real but won't be worked. |
| `kato:triage:invalid` | Outcome: not a real issue. |
| `kato:triage:needs-info` | Outcome: not enough info to act on. |
| `kato:triage:blocked` | Outcome: blocked by something external. |
| `kato:triage:question` | Outcome: a question, not a task. |

**Cross-platform tag mutation.** Native APIs are used where available (YouTrack, Jira, GitHub Issues, GitLab Issues). Platforms without native tag support (Bitbucket Issues today) fall through to a structured comment-marker fallback — kato posts `<!-- kato-tag {"action": "add", "tag": "..."} -->` as a comment.
