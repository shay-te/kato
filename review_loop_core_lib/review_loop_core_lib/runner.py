"""One review loop for one task, driven on its own thread.

    [self_check] the main chat reviews + fixes its own change (≤ N turns)
    begin round n ─▶ wait for the chat to settle ─▶ review the whole diff
                     (told the decisions so far; settled repeats don't block;
                      a clean-room confirmation is told nothing)
      no blocking finding:
        [verify_tests] the chat runs the tests ─▶ failing: fix them, round n+1
        [confirm_clean] the reviewer saw the ledger ─▶ clean-room round n+1
        otherwise ........................... done: CLEAN
      claimed fixes all still there ........ done: STUCK       (nothing sent)
      n is the last round .................. done: MAX_ROUNDS  (nothing sent)
      otherwise ─▶ wait for the chat ─▶ send the findings ─▶ wait for the fix
                 ─▶ read its decisions ─▶ begin round n+1
    any cancel ─▶ STOPPED (or INTERRUPTED)    anything broken ─▶ FAILED

Every wait is a ``cancel_event`` wait, so a Stop ends the loop within one poll
and a running review is killed through the reviewer's own cancel handling. The
state is saved after every step, so what an operator sees is never more than
one step behind.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, replace
from typing import Callable

from review_loop_core_lib.review_loop_core_lib.chat_prompts import (
    build_self_check_prompt,
    build_tests_fix_prompt,
    build_tests_prompt,
)
from review_loop_core_lib.review_loop_core_lib.data.state import (
    ReviewLoopPhase,
    ReviewLoopState,
    ReviewLoopStatus,
    ReviewRound,
    SelfCheckTurn,
    TestReport,
)
from review_loop_core_lib.review_loop_core_lib.diff import (
    DEFAULT_BUDGET_CHARS,
    candidate_digest,
    render_review_diff,
)
from review_loop_core_lib.review_loop_core_lib.findings_prompt import (
    build_findings_prompt,
)
from review_loop_core_lib.review_loop_core_lib.ports import (
    ChatChannel,
    ChatTurnEnd,
    LoopObserver,
    LoopWording,
    Readiness,
    Reviewer,
    TaskDiffSource,
)
from review_loop_core_lib.review_loop_core_lib.response import (
    parse_fix_response,
    parse_self_check,
    parse_test_report,
)
from review_loop_core_lib.review_loop_core_lib.reviewer_prompt import (
    build_reviewer_prompt,
)
from review_loop_core_lib.review_loop_core_lib.store import (
    ArtifactKind,
    ReviewLoopStore,
)
from review_loop_core_lib.review_loop_core_lib.verdict import (
    STUCK_AFTER_MISSED_FIXES,
    ReviewVerdict,
    ReviewVerdictError,
    count_missed_fixes,
    is_stuck,
    mark_repeats,
    parse_review_verdict,
    settle_findings,
)

DEFAULT_MAX_ROUNDS = 5
# The most reviews one loop may run, whatever is asked for. Hard changes take
# many rounds (a security hardening pass can take dozens); the operator picks.
MAX_ROUNDS_LIMIT = 30


@dataclass(frozen=True)
class RunnerOptions(object):
    """Timing knobs. The defaults are for real use; tests shrink them."""

    poll_seconds: float = 2.0
    # The chat must stay free this long before the loop uses it, so the
    # operator's own queued message (sent the moment a turn ends) goes first.
    settle_seconds: float = 5.0
    chat_wait_timeout_seconds: float = 7200.0
    fix_turn_timeout_seconds: float = 7200.0
    # A chat with no session at all for this long during a fix has ended.
    session_gone_grace_seconds: float = 90.0
    diff_budget_chars: int = DEFAULT_BUDGET_CHARS
    # The most self-check turns before the independent review starts anyway.
    self_check_turns: int = 3


class _Finish(Exception):
    """Unwinds the loop to its end with a final status."""

    def __init__(self, status: ReviewLoopStatus, reason: str = '') -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


class ReviewLoopRunner(object):
    """Drives ``state`` to a final status; see the module docstring."""

    def __init__(
        self,
        state: ReviewLoopState,
        *,
        diff_source: TaskDiffSource,
        reviewer: Reviewer,
        chat: ChatChannel,
        store: ReviewLoopStore,
        wording: LoopWording,
        task_summary: str = '',
        task_description: str = '',
        observer: LoopObserver | None = None,
        on_finished: Callable[[ReviewLoopRunner], None] | None = None,
        options: RunnerOptions | None = None,
        clock: Callable[[], float] = time.time,
        logger: logging.Logger | None = None,
    ) -> None:
        self._state = state
        self._task_id = state.task_id
        self._diff_source = diff_source
        self._reviewer = reviewer
        self._chat = chat
        self._store = store
        self._wording = wording
        self._task_summary = task_summary
        self._task_description = task_description
        self._observer = observer
        self._on_finished = on_finished
        self._options = options or RunnerOptions()
        self._clock = clock
        self._logger = logger or logging.getLogger('ReviewLoopRunner')
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._cancel_status = ReviewLoopStatus.STOPPED
        self._cancel_reason = ''
        self._thread: threading.Thread | None = None

    # ----- control -----

    @property
    def task_id(self) -> str:
        return self._task_id

    def start(self) -> None:
        self._save()
        self._notify('started')
        self._thread = threading.Thread(
            target=self._run, name=f'review-loop-{self._task_id}', daemon=True,
        )
        self._thread.start()

    def cancel(
        self, reason: str, *, status: ReviewLoopStatus = ReviewLoopStatus.STOPPED,
    ) -> None:
        with self._lock:
            if self._state.is_terminal or self._cancel.is_set():
                return
            self._cancel_status = status
            self._cancel_reason = reason
        self._cancel.set()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    @property
    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def snapshot(self) -> ReviewLoopState:
        with self._lock:
            return self._state.copy()

    # ----- the loop -----

    def _run(self) -> None:
        try:
            self._loop()
        except _Finish as finish:
            self._finish(finish.status, finish.reason)
        except Exception as exc:
            self._logger.exception('review loop for %s broke', self._task_id)
            self._finish(ReviewLoopStatus.FAILED, f'unexpected error: {exc}')
        finally:
            if self._on_finished is not None:
                self._on_finished(self)

    def _loop(self) -> None:
        """Rounds until one of them ends the loop — the last always does."""
        if self._state.self_check:
            self._self_check_phase()
        previous: set[str] | None = None
        # Per issue (fingerprint): how many fixes it has survived — the chat
        # said it fixed it (or did not answer) and the next review still found
        # it — and the id it was first sent under.
        missed: dict[str, int] = {}
        first_ids: dict[str, str] = {}
        max_rounds = self._state.max_rounds
        number = 0
        # The next review withholds the decision ledger: a clean-room check.
        confirm_next = False
        # The exact change the tests last PASSED on; a clean review of any
        # other code runs them again.
        tests_digest = ''
        while True:
            number += 1
            review_round = self._begin_round(number)
            self._wait_for_chat(ReviewLoopPhase.WAITING_TO_REVIEW)
            # This round is the clean-room sweep asked for by the last one.
            confirming = confirm_next
            verdict = self._review(review_round, withhold_ledger=confirming)
            confirm_next = False
            blocking = len(verdict.blocking)
            if verdict.is_clean:
                if self._state.verify_tests and tests_digest != review_round.diff_digest:
                    report = self._verify_tests(review_round)
                    if report.passed is False:
                        if number == max_rounds:
                            self._close_round(review_round, 'tests_failed')
                            raise _Finish(
                                ReviewLoopStatus.MAX_ROUNDS,
                                f'the tests still fail after {number} reviews',
                            )
                        self._fix_tests(review_round, report)
                        previous = None
                        continue
                    tests_digest = review_round.diff_digest
                # A clean verdict from a reviewer told the fix ping-pong
                # (``confirm_clean``) — or ANY clean verdict (``extra_sweep``) —
                # needs one more reviewer, told nothing, to agree. The sweep it
                # asks for is the last word.
                wants_sweep = not confirming and (
                    self._state.extra_sweep
                    or (self._state.confirm_clean and not review_round.blind)
                )
                if wants_sweep and number < max_rounds:
                    self._close_round(review_round, 'clean')
                    confirm_next = True
                    previous = None
                    continue
                # A clean verdict covers the code that reviewer saw — nothing
                # else. If the tree moved on meanwhile, review it again (the
                # same kind of review: a sweep is redone as a sweep).
                if self._code_changed_since(review_round):
                    self._close_round(review_round, 'changed')
                    self._notify('changed')
                    if number == max_rounds:
                        raise _Finish(
                            ReviewLoopStatus.MAX_ROUNDS,
                            f'the code changed after round {number} found it clean, '
                            'and no round was left to review it again',
                        )
                    confirm_next = confirming
                    previous = None
                    continue
                self._close_round(review_round, 'clean')
                raise _Finish(ReviewLoopStatus.CLEAN, self._clean_reason(
                    review_round, verdict, confirmed=confirming, no_room=wants_sweep,
                ))
            current = review_round.blocking_fingerprints
            if previous is not None:
                missed = count_missed_fixes(missed, previous, current)
            verdict = self._mark_repeats(review_round, verdict, missed, first_ids)
            # Stuck only when every blocking issue left has survived
            # STUCK_AFTER_MISSED_FIXES fixes: anything new, or a repeat that
            # has missed only once, goes back to the chat.
            if is_stuck(current, missed):
                self._close_round(review_round, 'stuck')
                raise _Finish(ReviewLoopStatus.STUCK, _stuck_reason(verdict))
            if number == max_rounds:
                self._close_round(review_round, 'max_rounds')
                raise _Finish(
                    ReviewLoopStatus.MAX_ROUNDS,
                    f'{blocking} blocking issue(s) left after {number} reviews',
                )
            for finding in verdict.blocking:
                first_ids.setdefault(finding.fingerprint, finding.id)
            prompt, dispatched_at = self._send_findings(review_round, verdict)
            self._await_fix(review_round, prompt, dispatched_at)
            # Only what the fixer claimed to fix (or left unanswered) can show
            # the loop is stuck: a finding it rejected with evidence is settled.
            previous = review_round.claimed_fixed_fingerprints

    def _mark_repeats(
        self, review_round: ReviewRound, verdict: ReviewVerdict,
        missed: dict[str, int], first_ids: dict[str, str],
    ) -> ReviewVerdict:
        """Mark the round's repeats (``mark_repeats``) on the saved round too,
        so the view shows them as well as the chat's message."""
        findings = mark_repeats(verdict.findings, missed, first_ids)
        with self._lock:
            review_round.findings = list(findings)
        self._save()
        return replace(verdict, findings=findings)

    def _clean_reason(
        self, review_round: ReviewRound, verdict: ReviewVerdict, *,
        confirmed: bool, no_room: bool,
    ) -> str:
        """Why the loop ended clean, with what backs it up."""
        reason = f'round {review_round.number} found no blocking issues'
        settled = len(verdict.settled)
        if settled:
            reason += f' ({settled} raised again were settled earlier)'
        notes = []
        if confirmed:
            notes.append('confirmed by a clean-room review')
        elif no_room:
            notes.append('no round left for a clean-room review')
        tests = review_round.tests or self._latest_tests()
        if self._state.verify_tests:
            notes.append(_tests_note(tests))
        return reason + ''.join(f'; {note}' for note in notes)

    def _code_changed_since(self, review_round: ReviewRound) -> bool:
        """Whether the task's change is no longer the one this round reviewed."""
        return candidate_digest(self._diff_source(self._task_id)) != review_round.diff_digest

    def _latest_tests(self) -> TestReport | None:
        with self._lock:
            for earlier in reversed(self._state.rounds):
                if earlier.tests is not None:
                    return earlier.tests
        return None

    def _begin_round(self, number: int) -> ReviewRound:
        review_round = ReviewRound(number=number, started_at=self._clock())
        with self._lock:
            self._state.rounds.append(review_round)
        self._save()
        return review_round

    def _review(self, review_round: ReviewRound, *, withhold_ledger: bool = False) -> ReviewVerdict:
        """One independent review of the whole change.

        ``withhold_ledger``: a clean-room check — the reviewer is told nothing
        of the decisions so far. Settled findings stay settled either way: that
        is the loop's call on the evidence, not something the reviewer decides.
        """
        self._set_phase(ReviewLoopPhase.REVIEWING)
        self._notify('reviewing')
        diffs = self._diff_source(self._task_id)
        rendered = render_review_diff(diffs, budget_chars=self._options.diff_budget_chars)
        with self._lock:
            review_round.diff_repos = rendered.repos
            review_round.diff_files = rendered.files
            review_round.diff_omitted = len(rendered.omitted_files)
            review_round.diff_digest = candidate_digest(diffs)
        if rendered.is_empty:
            raise _Finish(ReviewLoopStatus.FAILED, 'there are no changes to review')
        state = self.snapshot()
        self._store.write_artifact(state, review_round.number, ArtifactKind.DIFF, rendered.text)
        ledger = state.ledger()
        told = () if withhold_ledger else ledger
        with self._lock:
            review_round.blind = not told
            review_round.sweep = withhold_ledger
        prompt = build_reviewer_prompt(
            task_id=self._task_id,
            task_summary=self._task_summary,
            task_description=self._task_description,
            diffs=diffs,
            rendered=rendered,
            wording=self._wording,
            ledger=told,
        )
        try:
            reply = self._reviewer.review(
                prompt, task_id=self._task_id, cancel_event=self._cancel,
                model=self._state.model,
            )
        except Exception as exc:
            self._raise_if_cancelled()  # a Stop mid-review is a stop, not a failure
            raise _Finish(ReviewLoopStatus.FAILED, f'the review run failed: {exc}') from exc
        self._raise_if_cancelled()
        self._store.write_artifact(state, review_round.number, ArtifactKind.REVIEW, reply)
        try:
            verdict = parse_review_verdict(reply, round_number=review_round.number)
        except ReviewVerdictError as exc:
            raise _Finish(
                ReviewLoopStatus.FAILED, f'the reviewer\'s reply had no usable verdict ({exc})',
            ) from exc
        verdict = settle_findings(verdict, ledger)
        with self._lock:
            review_round.findings = list(verdict.findings)
            review_round.reviewed_at = self._clock()
        self._save()
        self._notify('reviewed')
        return verdict

    def _send_findings(
        self, review_round: ReviewRound, verdict: ReviewVerdict,
    ) -> tuple[str, float]:
        prompt = build_findings_prompt(
            task_id=self._task_id,
            verdict=verdict,
            round_number=review_round.number,
            max_rounds=self._state.max_rounds,
            wording=self._wording,
        )
        self._store.write_artifact(
            self.snapshot(), review_round.number, ArtifactKind.PROMPT, prompt,
        )
        dispatched_at = self._deliver(
            prompt, wait_phase=ReviewLoopPhase.WAITING_TO_SEND, label='findings',
        )
        with self._lock:
            review_round.sent_at = dispatched_at
            review_round.outcome = 'sent'
        self._set_phase(ReviewLoopPhase.AWAITING_FIX)
        self._notify('sent')
        return prompt, dispatched_at

    def _await_fix(self, review_round: ReviewRound, prompt: str, dispatched_at: float) -> None:
        turn = self._await_turn(prompt, dispatched_at, what='fix')
        self._record_responses(review_round, turn.text)
        with self._lock:
            review_round.fixed_at = turn.received_at
            review_round.finished_at = self._clock()
        self._notify('fixed')

    def _self_check_phase(self) -> None:
        """The main chat reviews and fixes its own change, before any reviewer.

        Up to ``self_check_turns`` turns, until the chat says this pass found
        nothing more. A reply without a readable block ends the phase too — a
        chat that cannot say it is done is not asked again and again; the
        independent review that follows catches what is left.
        """
        max_turns = max(1, int(self._options.self_check_turns))
        for number in range(1, max_turns + 1):
            turn = SelfCheckTurn(number=number, started_at=self._clock())
            with self._lock:
                self._state.self_checks.append(turn)
            self._save()
            prompt = build_self_check_prompt(turn=number, max_turns=max_turns, wording=self._wording)
            dispatched_at = self._deliver(
                prompt, wait_phase=ReviewLoopPhase.SELF_CHECK, label='self-check request',
            )
            self._notify('self_check')
            reply = self._await_turn(prompt, dispatched_at, what='self-check')
            if reply.text:
                self._store.write_artifact(
                    self.snapshot(), number, ArtifactKind.SELF_CHECK, reply.text,
                )
            clean, fixed, summary = parse_self_check(reply.text)
            with self._lock:
                turn.finished_at = reply.received_at
                turn.clean = clean
                turn.fixed = fixed
                turn.summary = summary
            self._save()
            self._notify('self_checked')
            if clean is not False:
                return

    def _verify_tests(self, review_round: ReviewRound) -> TestReport:
        """Have the main chat run the tests on the current tree, and report."""
        prompt = build_tests_prompt(wording=self._wording)
        dispatched_at = self._deliver(
            prompt, wait_phase=ReviewLoopPhase.VERIFYING, label='test request',
        )
        self._notify('verifying')
        reply = self._await_turn(prompt, dispatched_at, what='test run')
        if reply.text:
            self._store.write_artifact(
                self.snapshot(), review_round.number, ArtifactKind.TESTS, reply.text,
            )
        report = parse_test_report(reply.text, now=reply.received_at)
        with self._lock:
            review_round.tests = report
        self._save()
        self._notify('verified')
        return report

    def _fix_tests(self, review_round: ReviewRound, report: TestReport) -> None:
        """Send the failing tests back to the chat; the next review re-checks."""
        failures = report.failures or [report.summary or 'the tests failed']
        prompt = build_tests_fix_prompt(
            task_id=self._task_id, failures=failures, wording=self._wording,
        )
        self._store.write_artifact(
            self.snapshot(), review_round.number, ArtifactKind.PROMPT, prompt,
        )
        dispatched_at = self._deliver(
            prompt, wait_phase=ReviewLoopPhase.WAITING_TO_SEND, label='failing tests',
        )
        with self._lock:
            review_round.sent_at = dispatched_at
            review_round.outcome = 'tests_failed'
        self._set_phase(ReviewLoopPhase.AWAITING_FIX)
        self._notify('sent')
        turn = self._await_turn(prompt, dispatched_at, what='fix')
        with self._lock:
            review_round.fixed_at = turn.received_at
            review_round.finished_at = self._clock()
        self._notify('fixed')

    def _deliver(self, prompt: str, *, wait_phase: ReviewLoopPhase, label: str) -> float:
        """Wait until the chat is free, then send ``prompt`` under its lock.

        Returns when it was sent. ``label`` names the message in the failure.
        """
        while True:
            self._wait_for_chat(wait_phase)
            with self._chat.dispatch_lock(self._task_id):
                # Re-checked under the lock every sender holds: another one
                # may have taken the idle moment we waited for.
                readiness = self._chat.readiness(self._task_id)
                if readiness.state is Readiness.REFUSE:
                    raise _Finish(ReviewLoopStatus.STOPPED, readiness.reason)
                if readiness.state is not Readiness.READY:
                    continue
                stalled = self._chat.is_stalled(self._task_id)
                dispatched_at = self._clock()
                delivered = self._chat.deliver(self._task_id, prompt, force_respawn=stalled)
            break
        if not delivered:
            raise _Finish(
                ReviewLoopStatus.FAILED, f'the {label} could not be delivered to the chat',
            )
        return dispatched_at

    def _await_turn(self, prompt: str, dispatched_at: float, *, what: str) -> ChatTurnEnd:
        """Wait for the chat turn that ``prompt`` started to end; return it.

        ``what`` names the turn ("fix", "self-check", "test run") in the
        failure reasons.
        """
        options = self._options
        deadline = dispatched_at + options.fix_turn_timeout_seconds
        gone_since: float | None = None
        redelivered = False
        while True:
            self._raise_if_cancelled()
            turn = self._chat.turn_end_since(self._task_id, dispatched_at)
            if turn is not None:
                if turn.is_error:
                    raise _Finish(
                        ReviewLoopStatus.FAILED, f'the chat\'s {what} turn ended with an error',
                    )
                readiness = self._chat.readiness(self._task_id)
                if readiness.state is Readiness.READY:
                    self._set_waiting('')
                    return turn
                if readiness.state is Readiness.REFUSE:
                    raise _Finish(ReviewLoopStatus.STOPPED, readiness.reason)
                # The turn ended but the chat is still busy (background work
                # it started, or a message right behind it): wait for all of it.
                self._set_waiting(readiness.reason)
            elif not self._chat.session_alive(self._task_id):
                now = self._clock()
                gone_since = gone_since if gone_since is not None else now
                if now - gone_since >= options.session_gone_grace_seconds:
                    raise _Finish(
                        ReviewLoopStatus.FAILED,
                        f'the chat session ended before the {what} finished',
                    )
            else:
                gone_since = None
                approval = self._chat.pending_approval(self._task_id)
                # Checked BEFORE "stalled": a turn waiting on a person is
                # neither stuck nor to be respawned out from under them.
                self._set_waiting(
                    f'waiting for your approval in the chat ({approval})' if approval else '',
                )
                if not approval and self._chat.is_stalled(self._task_id):
                    if redelivered:
                        raise _Finish(
                            ReviewLoopStatus.FAILED,
                            f'the chat stopped reading its input twice during the {what}',
                        )
                    redelivered = True
                    with self._chat.dispatch_lock(self._task_id):
                        self._chat.deliver(self._task_id, prompt, force_respawn=True)
            if self._clock() >= deadline:
                raise _Finish(
                    ReviewLoopStatus.FAILED,
                    f'the {what} took longer than {int(options.fix_turn_timeout_seconds)}s',
                )
            self._sleep()

    def _record_responses(self, review_round: ReviewRound, reply: str) -> None:
        """The fix turn's answer to each finding it was sent — the ledger."""
        responses = parse_fix_response(reply, review_round.blocking)
        with self._lock:
            review_round.responses = responses
        if reply:
            self._store.write_artifact(
                self.snapshot(), review_round.number, ArtifactKind.RESPONSE, reply,
            )

    def _wait_for_chat(self, phase: ReviewLoopPhase) -> None:
        """Return once the chat has been free for ``settle_seconds``."""
        self._set_phase(phase)
        options = self._options
        deadline = self._clock() + options.chat_wait_timeout_seconds
        ready_since: float | None = None
        while True:
            self._raise_if_cancelled()
            readiness = self._chat.readiness(self._task_id)
            if readiness.state is Readiness.REFUSE:
                raise _Finish(ReviewLoopStatus.STOPPED, readiness.reason)
            now = self._clock()
            if readiness.state is Readiness.READY:
                ready_since = ready_since if ready_since is not None else now
                if now - ready_since >= options.settle_seconds:
                    self._set_waiting('')
                    return
                self._set_waiting('letting the chat settle')
            else:
                ready_since = None
                self._set_waiting(readiness.reason or 'the chat is busy')
            if now >= deadline:
                raise _Finish(
                    ReviewLoopStatus.FAILED,
                    f'the chat was not free for {int(options.chat_wait_timeout_seconds)}s',
                )
            self._sleep()

    # ----- state -----

    def _finish(self, status: ReviewLoopStatus, reason: str) -> None:
        now = self._clock()
        with self._lock:
            if self._state.is_terminal:
                return
            current = self._state.current_round
            if current is not None and not current.outcome:
                current.outcome = 'stopped' if status in (
                    ReviewLoopStatus.STOPPED, ReviewLoopStatus.INTERRUPTED,
                ) else 'failed'
            if current is not None and not current.finished_at:
                current.finished_at = now
            self._state.status = status
            self._state.phase = ReviewLoopPhase.DONE
            self._state.phase_started_at = now
            self._state.finished_at = now
            self._state.reason = reason
            self._state.waiting_for = ''
        self._save()
        self._store.prune(self._task_id)
        self._notify('finished')

    def _close_round(self, review_round: ReviewRound, outcome: str) -> None:
        with self._lock:
            review_round.outcome = outcome
            review_round.finished_at = self._clock()

    def _set_phase(self, phase: ReviewLoopPhase) -> None:
        with self._lock:
            if self._state.phase is phase:
                return
            self._state.phase = phase
            self._state.phase_started_at = self._clock()
            self._state.waiting_for = ''
        self._save()

    def _set_waiting(self, text: str) -> None:
        with self._lock:
            if self._state.waiting_for == text:
                return
            self._state.waiting_for = text
        self._save()

    def _save(self) -> None:
        self._store.save(self.snapshot())

    def _notify(self, event: str) -> None:
        if self._observer is None:
            return
        try:
            self._observer(self.snapshot(), event)
        except Exception:
            # An observer is a side channel (logging, notifications); it must
            # never be able to break the loop it is watching.
            self._logger.exception('review loop observer failed on %s', event)

    def _raise_if_cancelled(self) -> None:
        if self._cancel.is_set():
            raise _Finish(self._cancel_status, self._cancel_reason or 'stopped')

    def _sleep(self) -> None:
        if self._cancel.wait(self._options.poll_seconds):
            self._raise_if_cancelled()


def _tests_note(report: TestReport | None) -> str:
    if report is None or report.passed is None:
        return 'the tests were not reported'
    detail = report.summary or report.command
    return f'tests passed ({detail})' if detail else 'tests passed'


def _stuck_reason(verdict: ReviewVerdict) -> str:
    """Which issues the chat could not fix: "R2-1 still found after 2 fixes"."""
    ids = sorted({finding.repeat_of or finding.id for finding in verdict.blocking})
    return f'{", ".join(ids)} still found after {STUCK_AFTER_MISSED_FIXES} fixes'

