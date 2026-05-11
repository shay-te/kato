# Adopting an existing Claude session into kato

You started a Claude conversation somewhere else — VS Code Claude, the
JetBrains plugin, another kato instance — and now you want kato to pick
it up so the orchestrator can drive the rest of the task. The **Adopt
session** button in the kato chat header does this: it binds an
existing Claude session id to a kato task so the next agent spawn
resumes that conversation instead of starting fresh.

This document explains exactly what adoption does, what it does *not*
do, and the resulting failure modes you need to plan around. If you
came here because Claude inside kato seemed to "forget" what VS Code
Claude already did, the snapshot section below is the explanation.

## What adoption actually does

When you adopt a session id `<X>` for task `T`:

1. Kato persists `claude_session_id = <X>` on the per-task record so
   the next spawn passes `--resume <X>` to the Claude CLI.
2. Kato copies the source JSONL transcript
   (`~/.claude/projects/<encoded-source-cwd>/<X>.jsonl`) into kato's
   per-task workspace's projects dir
   (`~/.claude/projects/<encoded-kato-workspace-cwd>/<X>.jsonl`). Claude
   Code's session storage is keyed by cwd, so the file has to live
   under kato's spawn cwd for `--resume` to find it.
3. Kato spawns Claude at its **own per-task workspace clone** under
   `~/.kato/workspaces/<task>/<repo>/`, **not** at the source cwd.

Step 3 is the non-obvious one and the source of the trade-off below.

## The snapshot vs share-the-session trade-off

There are two ways to "adopt" a session, and kato chose snapshot:

### Snapshot (what kato does)

Kato copies the JSONL once into its workspace's projects dir. Future
turns kato handles write to **kato's** copy. Future turns the source
instance handles write to **the source's** copy. They diverge from
the moment of adoption.

Pros:

- Kato's git operations (checkout, commit, push) stay isolated to
  the workspace clone. Your VS Code working tree is never touched.
- You can review, rebase, or `git reset --hard` the workspace clone
  without losing work in your editor.
- One less foot-gun: you can't accidentally have two live `claude`
  subprocesses fighting over the same JSONL file.

Cons:

- The source instance and kato diverge after adoption. Anything VS
  Code Claude does post-adoption is invisible to kato, and vice
  versa.
- Kato's resumed Claude doesn't have the source's in-process working
  state. A broad prompt like "verify the changes" tends to ground
  itself by re-reading files / running git, rather than answering
  from memory the way the source instance would.

### Share (what kato does NOT do)

The alternative would be to spawn kato's Claude at the source cwd so
both processes read and write the same JSONL. Kato briefly tried
this; it broke the workspace-isolation guarantee — kato edited the
operator's live VS Code checkout in-place — and was reverted. If you
genuinely want a single shared session, you can do it manually by
opening VS Code at the kato workspace path
(`~/.kato/workspaces/<task>/<repo>`) so VS Code's session is *natively*
in the kato cwd. Both sides then write the same JSONL.

## What kato does to soften the snapshot's downsides

A few mitigations are baked in. Knowing they exist helps when you're
debugging a "Claude is acting weird after adoption" report.

1. **Resumed-session system-prompt addendum.** Every spawn appends a
   short note: "you may be resuming a conversation another instance
   started; trust the conversation's tool history; don't re-run
   ``git log`` / ``git diff`` / Reads on broad continuity questions."
   See `RESUMED_SESSION_ADDENDUM` in
   `sandbox_core_lib/sandbox_core_lib/system_prompt.py`. It's a nudge, not a
   block — Claude still uses git when there's a real reason.

2. **Workspace inventory block on every chat message.** The first
   thing every kato-handled prompt sees is a list of repos available
   in the workspace, with explicit instruction: "when the operator
   refers to 'the front end', resolve it against this list, do not
   assume similarly-named repos exist elsewhere." This stops Claude
   from latching onto names from `KATO_IGNORED_REPOSITORY_FOLDERS`
   when the actual workspace already has the repo. See
   `agent_prompt_utils.workspace_inventory_block`.

3. **`--add-dir` for sibling repos.** Multi-repo tasks expose every
   workspace clone (cwd + all `--add-dir` paths) so cross-repo
   questions work. Without this, Claude only sees its cwd and refuses
   cross-repo work. See `_chat_additional_dirs` in
   `webserver/kato_webserver/app.py`.

4. **Stable session id across respawns.** `claude --resume <id>`
   keeps the same session id by default (forking is opt-in via
   `--fork-session`); kato relies on that, so the session chip in
   the UI stays stable through Stop / Resume / kato restarts.

## Why "Claude is re-reading files / running git after adoption"

This is the most common adoption complaint and it has two layers.

**Cross-process state loss.** Kato's Claude is a different OS process
from the one that originally drove the conversation. The JSONL gives
it the *conversation* (every user / assistant / tool message); it
doesn't give it the *derived state* the source had at the end of its
last turn — the in-RAM confidence about file contents, the side
effects of recent tool calls, the "I just touched these N files"
feeling. So when a fresh subprocess gets "verify the changes," it
asks: do I trust the JSONL's tool entries, or do I read the disk?

**Defensive grounding.** A resumed subprocess leans toward reading
the disk. It's not wrong — files could have been edited externally
(another developer, your own editor, a `git pull`) since the JSONL
was last appended to. But for routine continuity questions in an
otherwise-clean workspace, the grounding pass is wasted work.

The system-prompt addendum above mitigates the second layer for
broad prompts. The first layer is fundamental — same physical Claude
process is the only thing that keeps RAM warm, and that's a property
of "long-lived editor sidekick" (VS Code Claude) vs "spawn-on-demand
orchestrator" (kato).

### Practical advice when adopting from VS Code

- **Phrase prompts narrowly after adoption.** "List the files you
  edited in the last 2 turns and the diff for each" reads from
  JSONL tool history. "Verify all the changes in the front end"
  reads "go inspect everything." Same instinct VS Code Claude would
  have on the same wording — it just had the answer cached in RAM.

- **Don't `git pull` between VS Code and kato if the same Claude
  session is supposed to own the edits.** Two writers (VS Code
  Claude editing your checkout, kato Claude editing the workspace
  clone) plus a `git pull` from one into the other is exactly the
  shape that makes Claude defensive — files change underneath it,
  it can't tell whether they match the JSONL's tool entries, so it
  falls back on git inspection.

- **Close the VS Code chat tab for the session you adopt before
  sending your first kato prompt.** Two live subprocesses on one
  session id is a split-brain scenario. The AdoptSessionModal warns
  about this; it isn't a soft warning, it's load-bearing.

- **Expect the first turn after adoption to be slower.** A fresh
  subprocess pays a cold prompt-cache cost on its first turn even
  with `--resume`. Subsequent turns to the same kato session reuse
  the cache.

## Appendix: when adoption was loaded incorrectly

If you adopted before kato 4.x's Windows path encoding fix, the
JSONL was migrated under an encoded folder name with the drive
colon left intact (`C:-Codes-...` instead of `C--Codes-...`).
`claude --resume <id>` looked under the correct encoded name, found
nothing, and silently started a fresh conversation. Re-adopt with
the latest build to fix. Verification: open the system bubble
that appears in the chat after adoption — if it shows a kato
workspace clone path under cwd and the session id matches what VS
Code reports, the migration landed.

If you see a constantly-changing session id across respawns, that
was an earlier kato regression where the spawn passed
`--session-id <X> --resume <X>` together. Claude rejects the
duplicate, kato's stale-resume self-heal kicked in, and the spawn
came up with a fresh id — clobbering the adopted one. Reverted.
Re-adopt to get the original id back.
