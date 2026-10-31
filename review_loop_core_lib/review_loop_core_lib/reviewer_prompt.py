"""The prompt for one independent, report-only review of a task's changes.

The reviewer is a FRESH agent: it did not write the change and shares no
history with the agent that did — that is the point of having it. It may read
anything in the task's repositories but change nothing, and it ends its reply
with a machine-readable verdict the loop decides on (see ``verdict.py``).

Fresh eyes have one weakness: left to themselves they find something on every
read, and each round's fix is more code for the next round to find something
in. So a review is held to the task, not to everything that could be better.
Every finding says how it belongs to the task (``relevance``), and one that
does not belong is reported without blocking.

After the first review, a round re-checks only what moved since the last one
and is told the decisions so far — what the fixer says it fixed (and the test
that proves it), and what it rejected or ruled out of scope (and why). The
ledger stops a settled argument from being restarted without new evidence.
"""

from __future__ import annotations

from review_loop_core_lib.review_loop_core_lib.data.state import (
    FINDING_CATEGORIES,
    FindingDecision,
    LedgerEntry,
)
from review_loop_core_lib.review_loop_core_lib.diff import RenderedDiff
from review_loop_core_lib.review_loop_core_lib.ports import LoopWording, RepoDiff

VERDICT_OPEN = '<review-verdict>'
VERDICT_CLOSE = '</review-verdict>'

# What a review checks — shared by the independent reviewer and the main
# chat's own self-check, so the two hold the change to the same bar.
CHECKLIST = '''1. Correctness — wrong logic or conditions, off-by-one, unhandled empty/None,
   broken edge cases, behaviour that does not do what the task asks.
2. Security — injection, path traversal, secrets in code or logs, missing
   authorization, untrusted input reaching a shell, SQL or the filesystem.
3. Error handling — swallowed errors, failures reported as success, missing
   cleanup, partial writes.
4. Concurrency — races, missing locks, shared mutable state, ordering bugs.
5. Tests — what the task delivers has no test; tests that cannot fail; mocks
   that hide the bug.
6. Contracts — changed signatures, payloads or schemas whose callers or stored
   data were not updated.
7. Performance — work on hot paths, N+1 queries, unbounded loops or memory.
8. Architecture — code in the wrong layer, a re-implemented existing helper,
   dead code left behind.
9. Observability — failures that leave no log line, or a misleading one.
10. Scope — changes the task did not ask for.'''

# The bar for BLOCKER / MAJOR, shared the same way.
NEVER_BLOCKS = '''None of these can block, however true they are:
- what the code already did before this change, including code the change only
  moved or copied;
- a case the task does not mention and its users would not hit in normal use;
- hardening, options, retries, validation or abstractions the task does not
  need;
- a missing test for one branch or error path, when what the task delivers is
  tested;
- proof nobody can give from here ("never ran on the real stack").'''

REVIEWER_INSTRUCTIONS = f'''You are an independent code reviewer. You did not write this change and you
have no history with it. Review the task's changes below and REPORT what you
find. Do not fix anything: this run cannot edit files or run commands, and must
not try.

## What this review is for
One question: does this change do what the task asks, without breaking anything
that worked? The task is the yardstick and the limit. Everything a finding asks
for is more code to write, test and keep, so what the task does not need, this
review does not ask for.

## What to check
{CHECKLIST}

The diff shows only what changed: read the files around it for context. Rules
files in the repositories (AGENTS.md, CLAUDE.md, CONTRIBUTING, README) are
binding — a line this change adds that breaks one is a finding.

## Severity
- BLOCKER — the change breaks production, loses data, or opens a security hole.
- MAJOR — the change does not do what the task asks, or has a real bug that
  someone using what the task built will hit. Must be fixed before merge.
- MINOR — worth improving; the task can ship without it.
- NIT — style or naming.
Report only what you can point to in the code. No speculation, no praise.

{NEVER_BLOCKS}
Report such a thing only if the operator should know about it, with `relevance`
"unrelated".

## Your reply
First a short readable report, one line per finding:
`SEVERITY repository/file:line — title. Fix: one line.`
Then end the reply with exactly one verdict block and nothing after it:

{VERDICT_OPEN}
{{"findings": [{{"severity": "MAJOR", "repository": "repository id", "file": "path/in/the/repository", "line": 42, "symbol": "enclosing_function_or_class", "category": "correctness", "title": "short title", "detail": "what is wrong, and the fix in one line", "invariant": "the rule the code must keep", "relevance": "task", "new_evidence": ""}}]}}
{VERDICT_CLOSE}

- `severity` is BLOCKER, MAJOR, MINOR or NIT.
- `category` is one of: {", ".join(FINDING_CATEGORIES)}.
- `symbol` is the enclosing function or class ("" at module level).
- `invariant` states the rule that is broken, as something that must hold
  ("divide raises ValueError when b is 0") — so the fix covers every place
  that must keep it, not just the line you name.
- `relevance` says how the finding belongs to this task: "task" (the change
  does not do what the task asks), "regression" (the change broke something
  that worked, or brought a bug of its own) or "unrelated" (anything else). An
  unrelated finding is shown to the operator and never sent to be fixed.
- `new_evidence` is only for raising again an issue an earlier round settled
  (see "Decisions so far", when present); leave it "" otherwise.
- An empty `findings` list means you found nothing worth reporting.
'''

RECHECK_RULES = '''## This round is a re-check
An earlier review already read the whole change, and its findings went to the
agent that writes the code. Read only what changed since, below, and answer two
questions: did each fix work, and did these changes break anything, in these
files or in the code that uses them? The rest was reviewed already: a finding
these changes did not cause is "unrelated".

Each file shows how its diff moved since the last review: a first-column `+` is
new since then, `-` is gone since then, a space is unchanged, and `…` is a gap.
The rest of each line reads as in the diff against the base. Line numbers are
not shown: read the file for them. A file the last review was not shown comes as
a plain diff.
'''

LEDGER_RULES = '''## Decisions so far
Earlier rounds of this loop sent findings to the agent that writes the code,
and it answered each one as below:
- "fixed": check the fix and its test. Report it again only if it is not
  actually fixed — say what is still wrong.
- "rejected" / "out_of_scope": the agent gave its evidence. Do not report the
  issue again unless you have NEW evidence that the decision is wrong; then
  put that evidence in the finding's `new_evidence`. Without it, a repeat is
  treated as settled.
- "unanswered": the agent did not say — judge the code as it is now.
'''

_DECISION_WORDS = {
    FindingDecision.FIXED: 'fixed',
    FindingDecision.REJECTED: 'rejected',
    FindingDecision.OUT_OF_SCOPE: 'out_of_scope',
    FindingDecision.UNANSWERED: 'unanswered',
}


def build_reviewer_prompt(
    *,
    task_id: str,
    task_summary: str,
    task_description: str,
    diffs: list[RepoDiff],
    rendered: RenderedDiff,
    wording: LoopWording,
    ledger: list[LedgerEntry] | tuple[LedgerEntry, ...] = (),
    recheck: RenderedDiff | None = None,
) -> str:
    """The full prompt for one review round.

    ``recheck``: how the change moved since the last review. Given, the round
    reads that instead of the whole change in ``rendered``.

    Ticket text, the diff and the fixer's decisions are untrusted inputs: all
    are framed with ``wording.wrap_untrusted`` so instructions hidden in them
    stay data.
    """
    parts = [REVIEWER_INSTRUCTIONS]
    if wording.reviewer_guidance.strip():
        parts.append(f'\n## Also\n{wording.reviewer_guidance.strip()}\n')
    parts.append(f'\n## The task\nTask: {task_id}\n')
    ticket = '\n\n'.join(
        text for text in (task_summary.strip(), task_description.strip()) if text
    )
    if ticket:
        parts.append(wording.wrap_untrusted(ticket, f'task {task_id} description'))
    parts.append('\n## Repositories\n')
    for repo in diffs:
        location = f' at {repo.cwd}' if repo.cwd else ''
        parts.append(f'- `{repo.repo_id}`{location}\n')
    if recheck is None:
        parts.append(
            f'\n## The changes ({rendered.files} file(s) in {rendered.repos} '
            'repository(ies), against each repository\'s base branch, including '
            'uncommitted and new files)\n',
        )
        parts.append(wording.wrap_untrusted(rendered.text, f'task {task_id} diff'))
    else:
        parts.append(f'\n{RECHECK_RULES}')
        parts.append(f'\n## What changed since the last review ({recheck.files} file(s))\n')
        parts.append(wording.wrap_untrusted(
            recheck.text, f'task {task_id} changes since the last review',
        ))
    if ledger:
        parts.append(f'\n\n{LEDGER_RULES}')
        parts.append(wording.wrap_untrusted(
            '\n'.join(_ledger_line(entry) for entry in ledger),
            f'task {task_id} review decisions so far',
        ))
    return ''.join(parts)


def _ledger_line(entry: LedgerEntry) -> str:
    finding, response = entry.finding, entry.response
    location = '/'.join(part for part in (finding.repository, finding.file) if part)
    symbol = f' ({finding.symbol})' if finding.symbol else ''
    line = (f'- {finding.id} [{finding.severity.value}] {location}{symbol} — '
            f'{finding.title}: {_DECISION_WORDS[response.decision]}')
    if response.test:
        line += f'; test: {response.test}'
    if response.evidence:
        line += f'; evidence: {response.evidence}'
    return line
