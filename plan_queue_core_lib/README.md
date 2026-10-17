# plan_queue_core_lib

Split an approved plan into small steps and run them as a queue: each step
starts only once every step it depends on is **done and merged**.

A **plan** goes through:

1. **Propose** — a read-only agent reads the plan and replies with a
   `<task-split>` block: steps with a title, a self-contained brief, the steps
   each depends on, and the repositories it may touch. A reply that can't be
   used is shown to the agent once more with the exact reason.
2. **Review** — the operator approves, edits, or asks for a revision with
   feedback. Proposals and edits pass the same validator (unique ids, known
   dependencies, no cycles, allowed repositories).
3. **Approve** — the host may create every step's task up front (a tracker
   ticket, left unassigned) or create each one only when it is released.
4. **Run** — a background tick tracks released tasks, looks for each done
   step's merge (with backoff), unblocks steps whose dependencies are all
   satisfied, and releases ready steps to the host. There is no cap of its own:
   the host's limits apply.

A step is **satisfied** when its task is done AND every repository it changed
is merged — or when the operator counts it as done. Change requests created
before the step was released never count (a branch name can repeat).

Nothing runs before the operator presses Run; after a restart a running plan is
paused until Run is pressed again; stopping a plan never touches a task that
already started.

## One capability per module

| Module | Capability |
|---|---|
| `data/plan.py` | the plan, its steps, their statuses (plain data) |
| `graph.py` | the single split validator, step ordering |
| `proposer_prompt.py` / `split.py` | asking for a split / reading it strictly |
| `proposing.py` | the background proposal run (repair once, drop stale results) |
| `step_text.py` | the description a step's task is given |
| `launching.py` | every call to the host's launcher, as outcomes |
| `progress.py` | a released step's status from its task's phase |
| `merge.py` | merged / waiting / declined, from change requests |
| `release.py` | the dependency rule: what may start |
| `ticking.py` | the background pass that composes the above |
| `registry.py` / `store.py` | plans in memory under one lock / on disk |
| `service.py` | operator actions; composes the rest, no logic of its own |

## What a host provides (`ports.py`)

- `SplitProposer` — runs a read-only agent on a prompt, returns its reply
- `SplitWording` — frames the plan as untrusted text; adds product guidance
- `TaskLauncher` — `prepare` (create, not started), `release` (start),
  `withdraw` (leave a note on a prepared task that won't run)
- `TaskProgress` — where each released task stands (`TaskPhase`)
- `PullRequestProbe` — every change request of a task's branch per repository;
  raises `ProbeError` when it can't ask (never `[]` for "could not ask")

```python
service = PlanQueueService(
    store=PlanQueueStore(root), proposer=..., wording=..., progress=..., probe=...,
)
service.bind_launcher(launcher)     # nothing is created or started before this
service.mark_interrupted('restarted')
service.start()                     # the background tick
plan = service.propose('TASK-1', plan_markdown=text, repository_ids=['api', 'web'])
```
