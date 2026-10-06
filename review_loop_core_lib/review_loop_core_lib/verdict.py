"""Read the reviewer's verdict, and decide whether a loop is going anywhere.

The loop's decisions — stop clean, send findings, give up as stuck — all hang
on the verdict block, so it is parsed strictly: a reply without a readable
block, or with a finding whose severity is not one of the four, is an error,
never a guess. A guessed "clean" would end the loop on a broken change; a
guessed "blocking" would send the fixer chasing nothing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable

from review_loop_core_lib.review_loop_core_lib.data.state import (
    BLOCKING_SEVERITIES,
    FINDING_CATEGORIES,
    FindingSeverity,
    LedgerEntry,
    ReviewFinding,
    severity_counts,
)
from review_loop_core_lib.review_loop_core_lib.reviewer_prompt import (
    VERDICT_CLOSE,
    VERDICT_OPEN,
)


class ReviewVerdictError(ValueError):
    """The reviewer's reply did not carry a usable verdict."""


@dataclass(frozen=True)
class ReviewVerdict(object):
    findings: tuple[ReviewFinding, ...]

    @property
    def blocking(self) -> tuple[ReviewFinding, ...]:
        return tuple(finding for finding in self.findings if finding.is_blocking)

    @property
    def settled(self) -> tuple[ReviewFinding, ...]:
        """Re-raised findings an earlier decision already settled."""
        return tuple(finding for finding in self.findings if finding.settled_by)

    @property
    def is_clean(self) -> bool:
        """No BLOCKER and no MAJOR — MINOR / NIT never keep a loop going."""
        return not self.blocking

    @property
    def counts(self) -> dict[str, int]:
        return severity_counts(self.findings)


def parse_review_verdict(text: str, *, round_number: int = 0) -> ReviewVerdict:
    """The LAST verdict block in ``text`` (a reviewer quoting the format
    earlier in its reply must not be read as its answer).

    With ``round_number``, each finding gets its id, ``R<round>-<n>``, in the
    order the reviewer listed them — what the fixer's decisions refer to.
    """
    body = _last_block(text or '')
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise ReviewVerdictError(f'the verdict block is not valid JSON: {exc}') from exc
    if not isinstance(payload, dict) or not isinstance(payload.get('findings'), list):
        raise ReviewVerdictError('the verdict block has no "findings" list')
    findings = [_finding(item) for item in payload['findings']]
    if round_number:
        findings = [
            finding.with_changes(id=f'R{round_number}-{index}')
            for index, finding in enumerate(findings, start=1)
        ]
    return ReviewVerdict(tuple(findings))


def settle_findings(verdict: ReviewVerdict, ledger: Iterable[LedgerEntry]) -> ReviewVerdict:
    """Mark blocking findings an earlier decision already settled.

    A fixer that rejected a finding (or ruled it out of scope) WITH evidence
    has answered it. A fresh reviewer that raises the same issue again
    without ``new_evidence`` would only restart that argument — and, before
    this, end the loop as "stuck" on an issue that was decided. Such a
    finding stays in the round's report, settled, and does not block. With
    new evidence it blocks again and goes back to the fixer.
    """
    settled = {
        entry.response.fingerprint: entry.response.finding_id
        for entry in ledger if entry.response.settles
    }
    if not settled:
        return verdict
    return ReviewVerdict(tuple(
        finding.with_changes(settled_by=settled[finding.fingerprint])
        if (finding.severity in BLOCKING_SEVERITIES
            and finding.fingerprint in settled
            and not finding.new_evidence)
        else finding
        for finding in verdict.findings
    ))


def is_stuck(previous: Iterable[str], current: Iterable[str]) -> bool:
    """True when a fix round fixed none of the previous round's blocking issues.

    ``previous`` / ``current`` are blocking-finding fingerprints. Every issue
    from before is still there (new ones may have joined): another round
    would only repeat the same request, so the loop stops instead of burning
    the rest of its rounds.
    """
    before = set(previous)
    return bool(before) and before <= set(current)


def _last_block(text: str) -> str:
    end = text.rfind(VERDICT_CLOSE)
    start = text.rfind(VERDICT_OPEN, 0, end if end >= 0 else len(text))
    if start < 0 or end < 0 or end < start:
        raise ReviewVerdictError('the reply has no review-verdict block')
    return text[start + len(VERDICT_OPEN):end].strip()


def _finding(item) -> ReviewFinding:
    if not isinstance(item, dict):
        raise ReviewVerdictError('a finding is not an object')
    raw_severity = str(item.get('severity', '')).strip().upper()
    try:
        severity = FindingSeverity(raw_severity)
    except ValueError as exc:
        raise ReviewVerdictError(f'unknown severity {raw_severity!r}') from exc
    category = str(item.get('category', '')).strip().lower()
    try:
        line = int(item.get('line', 0) or 0)
    except (TypeError, ValueError):
        line = 0
    return ReviewFinding(
        severity=severity,
        title=str(item.get('title', '')).strip() or '(untitled finding)',
        file=str(item.get('file', '')).strip(),
        repository=str(item.get('repository', '')).strip(),
        line=max(line, 0),
        symbol=str(item.get('symbol', '')).strip(),
        category=category if category in FINDING_CATEGORIES else 'other',
        detail=str(item.get('detail', '')).strip(),
        invariant=str(item.get('invariant', '') or '').strip(),
        new_evidence=str(item.get('new_evidence', '') or '').strip(),
    )
