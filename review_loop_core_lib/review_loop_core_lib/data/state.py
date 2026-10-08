"""The review loop's value types: where a loop is, and what each round found.

Everything here is plain data with a JSON round trip, so a loop's state can be
saved after every step, shown to an operator, and read back after a restart
without the code that produced it.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any


class ReviewLoopStatus(str, Enum):
    """How a loop stands overall. Everything but RUNNING is final."""

    RUNNING = 'running'
    CLEAN = 'clean'              # a review found no blocking issue
    MAX_ROUNDS = 'max_rounds'    # still blocking issues after the last review
    STUCK = 'stuck'              # a fix round changed none of the blocking issues
    STOPPED = 'stopped'          # the operator (or the chat's mode) ended it
    FAILED = 'failed'            # something broke; ``reason`` says what
    INTERRUPTED = 'interrupted'  # the host restarted while it was running


TERMINAL_STATUSES = frozenset(
    status for status in ReviewLoopStatus if status is not ReviewLoopStatus.RUNNING
)


class ReviewLoopPhase(str, Enum):
    """Where a running loop is right now."""

    SELF_CHECK = 'self_check'                # the main chat reviews + fixes its own change
    WAITING_TO_REVIEW = 'waiting_to_review'  # waiting for the chat to settle
    REVIEWING = 'reviewing'                  # the reviewer is reading the diff
    WAITING_TO_SEND = 'waiting_to_send'      # findings ready, chat not free yet
    AWAITING_FIX = 'awaiting_fix'            # the main agent is fixing
    VERIFYING = 'verifying'                  # the main chat is running the tests
    DONE = 'done'


class FindingSeverity(str, Enum):
    BLOCKER = 'BLOCKER'
    MAJOR = 'MAJOR'
    MINOR = 'MINOR'
    NIT = 'NIT'


# Only these keep a loop going. MINOR / NIT are reported, never sent to fix.
BLOCKING_SEVERITIES = frozenset({FindingSeverity.BLOCKER, FindingSeverity.MAJOR})

# Fixed so a finding's fingerprint is stable between rounds; anything else the
# reviewer writes is filed under ``other``.
FINDING_CATEGORIES = (
    'correctness', 'security', 'error_handling', 'concurrency', 'tests',
    'api_contract', 'performance', 'architecture', 'observability', 'scope',
    'other',
)


def severity_counts(findings) -> dict[str, int]:
    """Findings per severity, plus ``SETTLED``: re-raised findings an earlier
    round's decision already settled. Those are counted apart, never under
    their severity, so a "blocking" count only ever counts what still blocks."""
    counts = {severity.value: 0 for severity in FindingSeverity}
    counts['SETTLED'] = 0
    for finding in findings:
        counts['SETTLED' if finding.settled_by else finding.severity.value] += 1
    return counts


class FindingDecision(str, Enum):
    """What the fixing agent did with a finding it was sent."""

    FIXED = 'fixed'                # changed the code; ``test`` names the regression test
    REJECTED = 'rejected'          # the finding is wrong; ``evidence`` says why
    OUT_OF_SCOPE = 'out_of_scope'  # real, but not this task's to fix; ``evidence`` says why
    UNANSWERED = 'unanswered'      # the fix turn did not say


# A decision that, WITH evidence, settles its finding: a later review that
# raises the same issue again without new evidence does not keep the loop going.
SETTLING_DECISIONS = frozenset({FindingDecision.REJECTED, FindingDecision.OUT_OF_SCOPE})


@dataclass(frozen=True)
class ReviewFinding(object):
    """One problem the reviewer reported, located well enough to fix."""

    severity: FindingSeverity
    title: str
    file: str = ''
    repository: str = ''
    line: int = 0
    symbol: str = ''
    category: str = 'other'
    detail: str = ''
    # ``R<round>-<n>``, given by the loop so a decision can name the finding.
    id: str = ''
    # The rule the code must keep ("divide raises ValueError when b == 0"), so
    # the fixer can check every place that must keep it, not just this line.
    invariant: str = ''
    # Set by a reviewer re-raising a finding an earlier round settled: what it
    # found that the decision did not account for.
    new_evidence: str = ''
    # The id of the earlier finding whose decision settles this one ('' if none).
    settled_by: str = ''
    # Set by the loop when a fix said it fixed this issue (or did not answer)
    # and a later review still found it: the id it was first sent under, and
    # how many fixes it has survived.
    repeat_of: str = ''
    missed_fixes: int = 0

    @property
    def is_blocking(self) -> bool:
        """BLOCKER / MAJOR, and not already settled by an earlier decision."""
        return self.severity in BLOCKING_SEVERITIES and not self.settled_by

    def with_changes(self, **changes) -> 'ReviewFinding':
        return replace(self, **changes)

    @property
    def fingerprint(self) -> str:
        """Identity of the issue across rounds, ignoring line moves.

        Built from WHERE the issue is (repository, file, enclosing symbol) and
        WHAT kind it is — not the line number, which shifts as soon as anything
        above it is edited, and not the wording, which a fresh reviewer never
        repeats exactly.
        """
        key = '|'.join(
            part.strip().lower()
            for part in (self.repository, self.file, self.symbol, self.category)
        )
        return hashlib.sha1(key.encode('utf-8')).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        return {
            'severity': self.severity.value,
            'title': self.title,
            'file': self.file,
            'repository': self.repository,
            'line': self.line,
            'symbol': self.symbol,
            'category': self.category,
            'detail': self.detail,
            'id': self.id,
            'invariant': self.invariant,
            'new_evidence': self.new_evidence,
            'settled_by': self.settled_by,
            'repeat_of': self.repeat_of,
            'missed_fixes': self.missed_fixes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'ReviewFinding':
        return cls(
            severity=FindingSeverity(data['severity']),
            title=str(data.get('title', '')),
            file=str(data.get('file', '')),
            repository=str(data.get('repository', '')),
            line=int(data.get('line', 0) or 0),
            symbol=str(data.get('symbol', '')),
            category=str(data.get('category', 'other')),
            detail=str(data.get('detail', '')),
            id=str(data.get('id', '')),
            invariant=str(data.get('invariant', '')),
            new_evidence=str(data.get('new_evidence', '')),
            settled_by=str(data.get('settled_by', '')),
            repeat_of=str(data.get('repeat_of') or ''),
            missed_fixes=int(data.get('missed_fixes') or 0),
        )


@dataclass(frozen=True)
class FindingResponse(object):
    """The fixer's answer to one finding it was sent."""

    finding_id: str
    fingerprint: str
    decision: FindingDecision
    evidence: str = ''
    test: str = ''

    @property
    def settles(self) -> bool:
        """Rejected or out of scope WITH evidence. A bare "I disagree" settles
        nothing: the next review is free to raise it again."""
        return self.decision in SETTLING_DECISIONS and bool(self.evidence.strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            'finding_id': self.finding_id,
            'fingerprint': self.fingerprint,
            'decision': self.decision.value,
            'evidence': self.evidence,
            'test': self.test,
            'settles': self.settles,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'FindingResponse':
        return cls(
            finding_id=str(data.get('finding_id', '')),
            fingerprint=str(data.get('fingerprint', '')),
            decision=FindingDecision(data.get('decision', FindingDecision.UNANSWERED.value)),
            evidence=str(data.get('evidence', '')),
            test=str(data.get('test', '')),
        )


@dataclass
class SelfCheckTurn(object):
    """One turn in which the main chat reviewed (and fixed) its own change.

    Cheap — the chat already holds the whole context — so it catches the easy
    bugs before the independent reviewer is paid to.
    """

    number: int
    started_at: float
    finished_at: float = 0.0
    #: The chat's own verdict: True = found nothing more to fix; None = it
    #: did not say (no readable block).
    clean: bool | None = None
    fixed: int = 0
    summary: str = ''

    def to_dict(self) -> dict[str, Any]:
        return {
            'number': self.number, 'started_at': self.started_at,
            'finished_at': self.finished_at, 'clean': self.clean,
            'fixed': self.fixed, 'summary': self.summary,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'SelfCheckTurn':
        clean = data.get('clean')
        return cls(
            number=int(data.get('number', 0)),
            started_at=float(data.get('started_at', 0.0)),
            finished_at=float(data.get('finished_at', 0.0)),
            clean=None if clean is None else bool(clean),
            fixed=int(data.get('fixed', 0) or 0),
            summary=str(data.get('summary', '')),
        )


@dataclass
class TestReport(object):
    """What the main chat reported after running the task's tests."""

    #: True = all passed, False = something failed, None = it did not report.
    passed: bool | None = None
    command: str = ''
    summary: str = ''
    failures: list[str] = field(default_factory=list)
    reported_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            'passed': self.passed, 'command': self.command,
            'summary': self.summary, 'failures': list(self.failures),
            'reported_at': self.reported_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'TestReport':
        passed = data.get('passed')
        return cls(
            passed=None if passed is None else bool(passed),
            command=str(data.get('command', '')),
            summary=str(data.get('summary', '')),
            failures=[str(item) for item in data.get('failures', []) or []],
            reported_at=float(data.get('reported_at', 0.0)),
        )


@dataclass(frozen=True)
class LedgerEntry(object):
    """The latest decision on one issue, with the finding it answered."""

    finding: ReviewFinding
    response: FindingResponse


@dataclass
class ReviewRound(object):
    """One review, and — when it found something — the fix that followed."""

    number: int
    started_at: float
    reviewed_at: float = 0.0     # the reviewer finished
    sent_at: float = 0.0         # the findings went into the chat
    fixed_at: float = 0.0        # the chat's fix turn ended
    # The round's last step ended — its review, tests, or fix turn, or the
    # loop stopping during it. 0 while it runs, and for a round a restart cut
    # off (its real end is unknown).
    finished_at: float = 0.0
    findings: list[ReviewFinding] = field(default_factory=list)
    # The fix turn's answer to each finding it was sent (empty until it ends).
    responses: list[FindingResponse] = field(default_factory=list)
    # A "clean-room" review: the reviewer was NOT told the decision ledger, so
    # it is a pair of eyes that never took part in the fix ping-pong. Round 1
    # is blind by nature; a confirmation round withholds the ledger on purpose.
    blind: bool = False
    # Asked for as a clean-room SWEEP — a confirmation of the clean round
    # before it (``confirm_clean`` / ``extra_sweep``). Always blind; a round
    # after a test fix can be blind without being one.
    sweep: bool = False
    # The test run the main chat reported after this round came back clean.
    tests: TestReport | None = None
    # What the review was given: repositories, changed files, and files whose
    # diffs were left out for size (the reviewer reads those itself).
    diff_repos: int = 0
    diff_files: int = 0
    diff_omitted: int = 0
    # Fingerprint of the exact change reviewed (``diff.candidate_digest``).
    diff_digest: str = ''
    # clean | sent | changed | tests_failed | stuck | max_rounds | stopped | failed
    outcome: str = ''

    @property
    def blocking(self) -> list[ReviewFinding]:
        return [finding for finding in self.findings if finding.is_blocking]

    @property
    def blocking_fingerprints(self) -> set[str]:
        return {finding.fingerprint for finding in self.blocking}

    @property
    def claimed_fixed_fingerprints(self) -> set[str]:
        """The blocking issues this round's fix did NOT settle: claimed fixed,
        or left unanswered. The next review shows whether they really went."""
        settled = {response.fingerprint for response in self.responses if response.settles}
        return self.blocking_fingerprints - settled

    @property
    def counts(self) -> dict[str, int]:
        return severity_counts(self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            'number': self.number,
            'started_at': self.started_at,
            'reviewed_at': self.reviewed_at,
            'sent_at': self.sent_at,
            'fixed_at': self.fixed_at,
            'finished_at': self.finished_at,
            'findings': [finding.to_dict() for finding in self.findings],
            'responses': [response.to_dict() for response in self.responses],
            'blind': self.blind,
            'sweep': self.sweep,
            'tests': self.tests.to_dict() if self.tests is not None else None,
            'counts': self.counts,
            'diff_repos': self.diff_repos,
            'diff_files': self.diff_files,
            'diff_omitted': self.diff_omitted,
            'diff_digest': self.diff_digest,
            'outcome': self.outcome,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'ReviewRound':
        return cls(
            number=int(data['number']),
            started_at=float(data.get('started_at', 0.0)),
            reviewed_at=float(data.get('reviewed_at', 0.0)),
            sent_at=float(data.get('sent_at', 0.0)),
            fixed_at=float(data.get('fixed_at', 0.0)),
            finished_at=float(data.get('finished_at', 0.0)),
            findings=[ReviewFinding.from_dict(item) for item in data.get('findings', [])],
            responses=[FindingResponse.from_dict(item) for item in data.get('responses', [])],
            blind=bool(data.get('blind', False)),
            sweep=bool(data.get('sweep', False)),
            tests=TestReport.from_dict(data['tests']) if data.get('tests') else None,
            diff_repos=int(data.get('diff_repos', 0)),
            diff_files=int(data.get('diff_files', 0)),
            diff_omitted=int(data.get('diff_omitted', 0)),
            diff_digest=str(data.get('diff_digest') or ''),
            outcome=str(data.get('outcome', '')),
        )


@dataclass
class ReviewLoopState(object):
    """A whole loop: its status, its current position, and every round so far."""

    task_id: str
    loop_id: str
    max_rounds: int
    started_at: float
    status: ReviewLoopStatus = ReviewLoopStatus.RUNNING
    phase: ReviewLoopPhase = ReviewLoopPhase.WAITING_TO_REVIEW
    phase_started_at: float = 0.0
    finished_at: float = 0.0
    # Why a waiting phase is still waiting ("the agent is mid-turn"), or why a
    # finished loop ended the way it did.
    waiting_for: str = ''
    reason: str = ''
    rounds: list[ReviewRound] = field(default_factory=list)
    # The operator's options for this loop. All off by default here — a host
    # opts in per loop.
    self_check: bool = False      # the main chat reviews + fixes its own change first
    verify_tests: bool = False    # "clean" also needs the tests to pass
    confirm_clean: bool = False   # a ledger-informed "clean" needs a clean-room review to agree
    extra_sweep: bool = False     # ANY "clean" needs one more clean-room review to agree
    # The model every review of this loop runs on; '' = the host's default.
    model: str = ''
    self_checks: list[SelfCheckTurn] = field(default_factory=list)

    @classmethod
    def new(
        cls, task_id: str, *, max_rounds: int, now: float,
        self_check: bool = False, verify_tests: bool = False, confirm_clean: bool = False,
        extra_sweep: bool = False, model: str = '',
    ) -> 'ReviewLoopState':
        return cls(
            task_id=task_id,
            loop_id=uuid.uuid4().hex,
            max_rounds=max_rounds,
            started_at=now,
            phase_started_at=now,
            self_check=self_check,
            verify_tests=verify_tests,
            confirm_clean=confirm_clean,
            extra_sweep=extra_sweep,
            model=str(model or '').strip(),
        )

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @property
    def round(self) -> int:
        """The current (or last) round's number; 0 before the first review."""
        return self.rounds[-1].number if self.rounds else 0

    @property
    def current_round(self) -> ReviewRound | None:
        return self.rounds[-1] if self.rounds else None

    def ledger(self) -> list[LedgerEntry]:
        """The latest decision on each issue (by fingerprint), oldest issue first.

        What a later review is told about earlier rounds, and what decides
        whether a re-raised finding is already settled.
        """
        latest: dict[str, LedgerEntry] = {}
        for review_round in self.rounds:
            by_id = {finding.id: finding for finding in review_round.findings}
            for response in review_round.responses:
                finding = by_id.get(response.finding_id)
                if finding is not None:
                    latest[response.fingerprint] = LedgerEntry(finding, response)
        return list(latest.values())

    @property
    def last_reviewed_round(self) -> ReviewRound | None:
        for review_round in reversed(self.rounds):
            if review_round.reviewed_at:
                return review_round
        return None

    def summary(self) -> dict[str, Any]:
        """The small, cheap view an indicator polls: position and outcome."""
        reviewed = self.last_reviewed_round
        return {
            'loop_id': self.loop_id,
            'status': self.status.value,
            'phase': self.phase.value,
            'round': self.round,
            'max_rounds': self.max_rounds,
            'started_at': self.started_at,
            'phase_started_at': self.phase_started_at,
            'finished_at': self.finished_at,
            'waiting_for': self.waiting_for,
            'reason': self.reason,
            'counts': reviewed.counts if reviewed is not None else None,
            'self_check': self.self_check,
            'verify_tests': self.verify_tests,
            'confirm_clean': self.confirm_clean,
            'extra_sweep': self.extra_sweep,
            'model': self.model,
            'self_check_turn': len(self.self_checks),
            'sweep': bool(self.rounds and self.rounds[-1].sweep),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.summary(),
            'task_id': self.task_id,
            'rounds': [review_round.to_dict() for review_round in self.rounds],
            'self_checks': [turn.to_dict() for turn in self.self_checks],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'ReviewLoopState':
        return cls(
            task_id=str(data['task_id']),
            loop_id=str(data['loop_id']),
            max_rounds=int(data['max_rounds']),
            started_at=float(data['started_at']),
            status=ReviewLoopStatus(data.get('status', ReviewLoopStatus.RUNNING.value)),
            phase=ReviewLoopPhase(data.get('phase', ReviewLoopPhase.DONE.value)),
            phase_started_at=float(data.get('phase_started_at', 0.0)),
            finished_at=float(data.get('finished_at', 0.0)),
            waiting_for=str(data.get('waiting_for', '')),
            reason=str(data.get('reason', '')),
            rounds=[ReviewRound.from_dict(item) for item in data.get('rounds', [])],
            self_check=bool(data.get('self_check', False)),
            verify_tests=bool(data.get('verify_tests', False)),
            confirm_clean=bool(data.get('confirm_clean', False)),
            extra_sweep=bool(data.get('extra_sweep', False)),
            model=str(data.get('model') or ''),
            self_checks=[SelfCheckTurn.from_dict(item) for item in data.get('self_checks', [])],
        )

    def copy(self) -> 'ReviewLoopState':
        return ReviewLoopState.from_dict(self.to_dict())
