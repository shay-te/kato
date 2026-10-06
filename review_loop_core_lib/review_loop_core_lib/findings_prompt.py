"""The message a round posts into the task's chat: the blocking findings, to fix.

Its FIRST line is the host's fixed header (``LoopWording.findings_header``), so
a UI can recognise the message as the loop's and not show it as something the
operator typed. Only BLOCKER / MAJOR findings are sent: MINOR and NIT stay in
the round's report, because asking the fixer to chase nits is how a loop that
should finish clean keeps going.

The findings are the reviewer's own words, and the reviewer read repository
content to write them — they go in framed as untrusted, like any other text
the fixer did not get from the operator.

The fixer answers every finding in a response block (``response.py`` reads
it): fixed, with the regression test that covers it, or rejected / out of
scope, with evidence. Those answers are the loop's decision ledger — the next
review is told them, and a rejection with evidence settles its finding.
"""

from __future__ import annotations

from review_loop_core_lib.review_loop_core_lib.ports import LoopWording
from review_loop_core_lib.review_loop_core_lib.verdict import ReviewVerdict

RESPONSE_OPEN = '<review-response>'
RESPONSE_CLOSE = '</review-response>'

FIXER_RULES = f'''How to handle them — decide each finding on its merits:
- Fix it: fix the ROOT CAUSE, not the line named. Where a finding states an
  invariant, check every place in the change that must keep it.
- Every fix gets a regression test that fails without the fix. Run the tests
  that cover it.
- Reject it only when it is wrong, or mark it out_of_scope when it is real but
  not this task's — and only with evidence you can point to: file:line, a test
  you ran, the docs, the library's own source. Never reject to avoid work.
- Keep each change minimal and in scope.
- Do not commit, push or run git — publishing is the operator's call.
- MINOR and NIT findings were left out on purpose; do not go looking for them.
- End your reply with exactly one response block, one entry per finding id
  above, and nothing after it:

{RESPONSE_OPEN}
{{"decisions": [{{"id": "R1-1", "decision": "fixed", "test": "path/to/test_file.py::test_name", "evidence": "what you changed"}}, {{"id": "R1-2", "decision": "rejected", "evidence": "why it is wrong, with a pointer to the proof"}}]}}
{RESPONSE_CLOSE}

`decision` is fixed, rejected or out_of_scope. The next review is told these
answers: a rejection with evidence is not raised again without new evidence.'''


def findings_header(wording: LoopWording, *, round_number: int, max_rounds: int) -> str:
    return wording.findings_header.format(round=round_number, max_rounds=max_rounds)


def build_findings_prompt(
    *,
    task_id: str,
    verdict: ReviewVerdict,
    round_number: int,
    max_rounds: int,
    wording: LoopWording,
) -> str:
    blocking = verdict.blocking
    lines = [
        findings_header(wording, round_number=round_number, max_rounds=max_rounds),
        '',
        f'An independent reviewer read the whole change and found {len(blocking)} '
        'blocking issue(s) (BLOCKER / MAJOR). Fix them now.',
        '',
    ]
    listed = '\n\n'.join(
        _finding_text(index, finding) for index, finding in enumerate(blocking, start=1)
    )
    body = '\n'.join(lines) + wording.wrap_untrusted(
        listed, f'task {task_id} review findings, round {round_number}',
    )
    rules = FIXER_RULES
    if wording.findings_guidance.strip():
        rules = f'{rules}\n{wording.findings_guidance.strip()}'
    return f'{body}\n\n{rules}\n'


def _finding_text(index: int, finding) -> str:
    location = '/'.join(part for part in (finding.repository, finding.file) if part)
    if location and finding.line:
        location = f'{location}:{finding.line}'
    where = f' {location}' if location else ''
    symbol = f' ({finding.symbol})' if finding.symbol else ''
    label = f'{finding.id} ' if finding.id else ''
    lines = [f'{index}. {label}[{finding.severity.value}]{where}{symbol} — {finding.title}']
    if finding.detail:
        lines.append(f'   {finding.detail}')
    if finding.invariant:
        lines.append(f'   Invariant: {finding.invariant}')
    if finding.new_evidence:
        lines.append(f'   Raised again with new evidence: {finding.new_evidence}')
    return '\n'.join(lines)
