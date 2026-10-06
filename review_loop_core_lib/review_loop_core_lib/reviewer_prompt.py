"""The prompt for one independent, report-only review of a task's changes.

The reviewer is a FRESH agent: it did not write the change and shares no
history with the agent that did — that is the point of having it. It may read
anything in the task's repositories but change nothing, and it ends its reply
with a machine-readable verdict the loop decides on (see ``verdict.py``).

From the second round on it is also told the decisions so far — what the
fixer says it fixed (and the test that proves it), and what it rejected or
ruled out of scope (and why). It still reviews the whole change on its own;
the ledger only stops a settled argument from being restarted without new
evidence.
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

REVIEWER_INSTRUCTIONS = f'''You are an independent code reviewer. You did not write this change and you
have no history with it. Review the task's changes below and REPORT what you
find. Do not fix anything: this run cannot edit files or run commands, and must
not try.

## What to check
1. Correctness — wrong logic or conditions, off-by-one, unhandled empty/None,
   broken edge cases, behaviour that does not do what the task asks.
2. Security — injection, path traversal, secrets in code or logs, missing
   authorization, untrusted input reaching a shell, SQL or the filesystem.
3. Error handling — swallowed errors, failures reported as success, missing
   cleanup, partial writes.
4. Concurrency — races, missing locks, shared mutable state, ordering bugs.
5. Tests — new or changed behaviour without a test; tests that cannot fail;
   mocks that hide the bug.
6. Contracts — changed signatures, payloads or schemas whose callers or stored
   data were not updated.
7. Performance — work on hot paths, N+1 queries, unbounded loops or memory.
8. Architecture — code in the wrong layer, a re-implemented existing helper,
   dead code left behind.
9. Observability — failures that leave no log line, or a misleading one.
10. Scope — changes the task did not ask for.

The diff shows only what changed: read the files around it for context. Rules
files in the repositories (AGENTS.md, CLAUDE.md, CONTRIBUTING, README) are
binding — breaking one is a finding.

## Severity
- BLOCKER — breaks production, loses data, or opens a security hole.
- MAJOR — a real bug, or new behaviour with no test. Must be fixed before merge.
- MINOR — worth improving; does not block.
- NIT — style or naming.
Report only what you can point to in the code. No speculation, no praise.

## Your reply
First a short readable report, one line per finding:
`SEVERITY repository/file:line — title. Fix: one line.`
Then end the reply with exactly one verdict block and nothing after it:

{VERDICT_OPEN}
{{"findings": [{{"severity": "MAJOR", "repository": "repository id", "file": "path/in/the/repository", "line": 42, "symbol": "enclosing_function_or_class", "category": "correctness", "title": "short title", "detail": "what is wrong, and the fix in one line", "invariant": "the rule the code must keep", "new_evidence": ""}}]}}
{VERDICT_CLOSE}

- `severity` is BLOCKER, MAJOR, MINOR or NIT.
- `category` is one of: {", ".join(FINDING_CATEGORIES)}.
- `symbol` is the enclosing function or class ("" at module level).
- `invariant` states the rule that is broken, as something that must hold
  ("divide raises ValueError when b is 0") — so the fix covers every place
  that must keep it, not just the line you name.
- `new_evidence` is only for raising again an issue an earlier round settled
  (see "Decisions so far", when present); leave it "" otherwise.
- An empty `findings` list means you found nothing worth reporting.
'''

LEDGER_RULES = '''## Decisions so far
Earlier rounds of this loop sent findings to the agent that writes the code,
and it answered each one as below. Still review the WHOLE change on its own
merits, and:
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
) -> str:
    """The full prompt for one review round.

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
    parts.append(
        f'\n## The changes ({rendered.files} file(s) in {rendered.repos} '
        'repository(ies), against each repository\'s base branch, including '
        'uncommitted and new files)\n',
    )
    parts.append(wording.wrap_untrusted(rendered.text, f'task {task_id} diff'))
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
