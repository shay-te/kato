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

from review_loop_core_lib.review_loop_core_lib.data.state import (
    FindingDecision,
    FindingResponse,
    ReviewFinding,
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


def _answers(text: str) -> dict[str, dict]:
    """The LAST response block's decisions by finding id; {} when unusable."""
    end = text.rfind(RESPONSE_CLOSE)
    start = text.rfind(RESPONSE_OPEN, 0, end if end >= 0 else len(text))
    if start < 0 or end < 0:
        return {}
    try:
        payload = json.loads(text[start + len(RESPONSE_OPEN):end].strip())
    except ValueError:
        return {}
    items = payload.get('decisions') if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return {}
    return {
        str(item.get('id', '')).strip().upper(): item
        for item in items if isinstance(item, dict)
    }


def _decision(raw) -> FindingDecision:
    key = str(raw or '').strip().lower().replace('-', '_').replace(' ', '_')
    return _DECISIONS.get(key, FindingDecision.UNANSWERED)
