"""Read the fixer's answer to the findings it was sent.

The findings message asks the fix turn to end with a response block: for
every finding, fixed (and the regression test that covers it), rejected, or
out of scope — the last two with evidence. Those answers are the loop's
decision ledger: a later review is told them, and a rejection with evidence
settles its finding (see ``verdict.settle_findings``).

Parsed leniently, unlike the reviewer's verdict: a fix turn that forgot the
block, or wrote it badly, still changed the code. Its findings are recorded as
unanswered and the next review judges the working tree — a loop never fails
over the fixer's formatting.
"""

from __future__ import annotations

import json
from typing import Iterable

from review_loop_core_lib.review_loop_core_lib.chat_prompts import (
    SELF_CHECK_CLOSE,
    SELF_CHECK_OPEN,
    TEST_REPORT_CLOSE,
    TEST_REPORT_OPEN,
)
from review_loop_core_lib.review_loop_core_lib.data.state import (
    FindingDecision,
    FindingResponse,
    ReviewFinding,
    TestReport,
)
from review_loop_core_lib.review_loop_core_lib.findings_prompt import (
    RESPONSE_CLOSE,
    RESPONSE_OPEN,
)

# What a fixer may write, and what it means. A few near-synonyms are accepted
# because a model will use them; anything else counts as no answer.
_DECISIONS = {
    'fixed': FindingDecision.FIXED,
    'rejected': FindingDecision.REJECTED,
    'not_applicable': FindingDecision.REJECTED,
    'out_of_scope': FindingDecision.OUT_OF_SCOPE,
    'deferred': FindingDecision.OUT_OF_SCOPE,
}


def parse_fix_response(text: str, sent: Iterable[ReviewFinding]) -> list[FindingResponse]:
    """One response per finding that was sent, in the order they were sent."""
    answers = _answers(text or '')
    responses = []
    for finding in sent:
        item = answers.get(finding.id.upper(), {})
        responses.append(FindingResponse(
            finding_id=finding.id,
            fingerprint=finding.fingerprint,
            decision=_decision(item.get('decision')),
            evidence=str(item.get('evidence', '') or '').strip(),
            test=str(item.get('test', '') or '').strip(),
        ))
    return responses


def parse_self_check(text: str) -> tuple[bool | None, int, str]:
    """``(clean, fixed, summary)`` from a self-check reply.

    ``clean`` is None when the reply carried no readable block — the loop then
    stops self-checking (a chat that cannot say it is done is not asked again
    and again) and moves on to the independent review.
    """
    payload = _last_block(text or '', SELF_CHECK_OPEN, SELF_CHECK_CLOSE)
    if payload is None:
        return None, 0, ''
    clean = payload.get('clean')
    try:
        fixed = max(int(payload.get('fixed', 0) or 0), 0)
    except (TypeError, ValueError):
        fixed = 0
    return (
        None if clean is None else bool(clean),
        fixed,
        str(payload.get('summary', '') or '').strip(),
    )


def parse_test_report(text: str, *, now: float = 0.0) -> TestReport:
    """The chat's test report; ``passed`` is None when it did not report one."""
    payload = _last_block(text or '', TEST_REPORT_OPEN, TEST_REPORT_CLOSE)
    if payload is None:
        return TestReport(reported_at=now)
    passed = payload.get('passed')
    failures = payload.get('failures')
    return TestReport(
        passed=None if passed is None else bool(passed),
        command=str(payload.get('command', '') or '').strip(),
        summary=str(payload.get('summary', '') or '').strip(),
        failures=[str(item).strip() for item in failures if str(item).strip()]
        if isinstance(failures, list) else [],
        reported_at=now,
    )


def _answers(text: str) -> dict[str, dict]:
    """The LAST response block's decisions by finding id; {} when unusable."""
    payload = _last_block(text, RESPONSE_OPEN, RESPONSE_CLOSE)
    items = payload.get('decisions') if payload is not None else None
    if not isinstance(items, list):
        return {}
    return {
        str(item.get('id', '')).strip().upper(): item
        for item in items if isinstance(item, dict)
    }


def _last_block(text: str, open_tag: str, close_tag: str) -> dict | None:
    """The JSON object in the LAST ``open_tag … close_tag`` block, or None.

    The last one wins: a reply that quotes the format earlier must not be read
    as its answer. Anything unusable — no block, bad JSON, not an object — is
    None, never an error.
    """
    end = text.rfind(close_tag)
    start = text.rfind(open_tag, 0, end if end >= 0 else len(text))
    if start < 0 or end < 0:
        return None
    try:
        payload = json.loads(text[start + len(open_tag):end].strip())
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _decision(raw) -> FindingDecision:
    key = str(raw or '').strip().lower().replace('-', '_').replace(' ', '_')
    return _DECISIONS.get(key, FindingDecision.UNANSWERED)
