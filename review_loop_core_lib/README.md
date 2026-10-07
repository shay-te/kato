# review_loop_core_lib

Review a change with fresh eyes, hand the findings to the agent that wrote it,
and repeat until a review comes back clean.

A **review loop** runs on one task:

1. An **independent reviewer** reads the task's whole diff (every repository,
   against its base, uncommitted and new files included). It is a fresh,
   read-only agent run with no history with the change, and it ends its reply
   with a machine-readable verdict.
2. If the verdict has no BLOCKER or MAJOR finding, the loop ends **clean**.
   MINOR / NIT findings are recorded but never keep the loop going.
3. Otherwise the blocking findings are posted into the task's **main chat**,
   each with an id (`R1-1`) and the invariant it breaks. The agent there
   decides each one: **fixed** (with the regression test that covers it), or
   **rejected** / **out_of_scope** with evidence (file:line, a test, the docs,
   a library's source). It ends its reply with a `<review-response>` block.
4. When that fix turn ends, its answers go into the **decision ledger**
   (`ReviewRound.responses`) and the next round reviews the whole diff again —
   told the decisions so far.

**Optional stages** (all off unless the host passes them to `start`):

- `self_check` — before the first review, the main chat reviews and fixes its
  own change (cheap: it holds the context), up to `self_check_turns`, until
  its `<self-check>` block says clean.
- `verify_tests` — a clean review ends the loop only once the main chat ran
  the tests and its `<test-report>` passes; failures go back to be fixed.
- `confirm_clean` — a clean verdict from a reviewer told the ledger needs a
  clean-room **sweep** (a fresh reviewer told nothing) to agree.
- `extra_sweep` — every clean verdict needs that sweep: two clean reviews in
  a row, the second blind.

**Clean means this exact code.** A round records a digest of the diff it
reviewed (`candidate_digest`). A clean verdict is accepted only if the diff
is still the same when the loop is about to finish; otherwise the round is
closed `changed` and the review is redone on the new code. Tests count as
passed only for the digest they ran on.

**The ledger.** A rejection or out-of-scope ruling *with evidence* settles its
finding. A later reviewer that raises the same issue again (same repository,
file, symbol and category) without `new_evidence` gets it marked settled: shown,
not sent, not blocking. With new evidence it blocks again and goes back to the
fixer. A bare "I disagree" settles nothing. Without this, a fresh reviewer
re-raising a decided issue ended the loop as **stuck**.

It stops on: clean · the round cap (picked per loop, 1–30 reviews; default 5,
so at most 4 fix rounds) · **stuck** (none of the issues the fixer claimed to
fix — or left unanswered — went away; settled ones don't count) · Stop · a
failure (with a reason) · a host restart (**interrupted**, never resumed). A
fix turn with no readable response block is not a failure: its findings are
recorded as unanswered and the next review judges the code.

## State machine

```
begin round n ─▶ wait for the chat to settle ─▶ review (told the ledger; settled repeats don't block)
  no blocking finding .................. CLEAN
  claimed fixes all still there ........ STUCK        (nothing sent)
  n is the last round .................. MAX_ROUNDS   (nothing sent)
  otherwise ─▶ wait for the chat ─▶ send (under the chat's lock) ─▶ wait for the fix
            ─▶ read its <review-response> into the ledger
any cancel ─▶ STOPPED / INTERRUPTED      anything broken ─▶ FAILED
```

`ReviewLoopState.phase` says where a running loop is (`waiting_to_review`,
`reviewing`, `waiting_to_send`, `awaiting_fix`), `phase_started_at` when it got
there, and `waiting_for` what a waiting phase is waiting on. That is what a UI
shows as "round 2 of 5 · fixing · 4m".

## What the host provides (`ports.py`)

| Port | Job |
|---|---|
| `ChatChannel` | Is the chat free (`readiness`: ready / wait / refuse), the per-task lock every sender holds, send-or-respawn (`deliver`), "did a turn end since T" (`turn_end_since`), and which tool the chat is waiting on the operator to approve (`pending_approval` — shown as "waiting for your approval", and never treated as a stall). |
| `Reviewer` | One fresh read-only review → the reviewer's full reply. Must stop soon after its `cancel_event` is set. |
| `TaskDiffSource` | `task_id → [RepoDiff]`, called once per round. |
| `LoopWording` | The host's untrusted-content framing (required), the findings header (first line of every posted message, so a UI can tell it apart from operator text), and any extra guidance for the reviewer and the fixer. |
| `LoopObserver` | Optional `(state, event)` callback after each step — logging, notifications. It can never break the loop. |

The lib imports only the standard library, `utils_core_lib` and nothing that
names a host. Everything product-specific comes in through these ports.

## Storage (`store.py`)

`<root>/<task_id>/<loop_id>/` holds `state.json` (rewritten after every step)
and, per round, the diff the reviewer saw, its full reply, and the message
posted to the chat. The newest 5 loops per task are kept. Ids are validated
before they touch a path, so an id from a request can never escape the root.

**Put the root outside the task's repositories.** Nothing here may be
committed, and a large diff artifact must not be scanned or mounted as part of
the task's workspace.

## Using it

```python
service = ReviewLoopService(
    store=ReviewLoopStore(root),
    chat=my_chat_channel,
    reviewer=my_reviewer,
    wording=LoopWording(wrap_untrusted=my_wrapper, findings_header='Review loop — round {round} of {max_rounds}'),
    observer=log_it,
    can_start=lambda task_id: '' if ok(task_id) else 'why not',
)
service.mark_interrupted('the host restarted')     # once, at boot
service.start(task_id, diff_source=diffs_for)      # ReviewLoopError(reason) if refused
service.start(task_id, diff_source=diffs_for, max_rounds=3)  # this loop's cap (held to 1..10)
service.summaries()                                # cheap; for an indicator
service.state(task_id)                             # full rounds; for a view
service.artifact(task_id, loop_id, round, 'review')
service.stop(task_id)                              # or forget(task_id) when the task is removed
```

## Tests

`tests/` — unit tests per module plus `test_flow.py` (whole loops through the
public service). 100% line and branch coverage.
