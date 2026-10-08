"""Resuming a loop that was cut off part-way, end to end through the service.

A loop stops in the middle of something — a review that timed out, a fix turn
the operator interrupted, a restart, two fixes that did not land — and
"Run again" threw all of it away: the decisions, the repeat counts, the round
number. Resume picks the SAME loop up at the step it was cut off in:

* a review that never finished is run again, in the same round;
* a chat that was mid-fix gets a "continue" nudge — the round's message is
  already in the chat and is never sent twice;
* findings or failing tests that never went out are sent;
* a cut-off self-check or test run is asked again.

Afterwards the loop judges every review exactly as if it had never stopped.

Real service + runner; only the chat and the reviewer are stand-ins.
"""
from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from review_loop_core_lib.review_loop_core_lib.chat_prompts import (
    SELF_CHECK_CLOSE,
    SELF_CHECK_OPEN,
    TEST_REPORT_CLOSE,
    TEST_REPORT_OPEN,
    build_continue_prompt,
)
from review_loop_core_lib.review_loop_core_lib.data.state import (
    FindingDecision,
    FindingResponse,
    FindingSeverity,
    ResumePoint,
    ResumeStage,
    ReviewFinding,
    ReviewLoopPhase,
    ReviewLoopState,
    ReviewLoopStatus,
    ReviewRound,
    SelfCheckTurn,
    TestReport,
)
from review_loop_core_lib.review_loop_core_lib.diff import candidate_digest
from review_loop_core_lib.review_loop_core_lib.findings_prompt import (
    RESPONSE_CLOSE,
    RESPONSE_OPEN,
)
from review_loop_core_lib.review_loop_core_lib.ports import ChatReadiness
from review_loop_core_lib.review_loop_core_lib.progress import loop_memory
from review_loop_core_lib.review_loop_core_lib.service import (
    ReviewLoopError,
    ReviewLoopService,
)
from review_loop_core_lib.review_loop_core_lib.store import ReviewLoopStore
from review_loop_core_lib.review_loop_core_lib.tests.fakes import (
    FAST,
    WORDING,
    FakeChat,
    FakeTree,
    finding,
    reply,
    wait_until,
)

_SENT_ID = re.compile(r'^\d+\. (R\d+-\d+) \[', re.MULTILINE)


def respond(*ids: str) -> str:
    """A fix turn's reply: every id fixed, with a test."""
    decisions = [
        {'id': finding_id, 'decision': 'fixed', 'test': 't.py::test_it', 'evidence': 'changed it'}
        for finding_id in ids
    ]
    body = json.dumps({'decisions': decisions})
    return f'Done.\n{RESPONSE_OPEN}\n{body}\n{RESPONSE_CLOSE}'


def fix_everything(prompt: str) -> str:
    """Answer a findings message: every finding it lists, fixed."""
    return respond(*_SENT_ID.findall(prompt))


def self_check(clean: bool) -> str:
    body = json.dumps({'clean': clean, 'fixed': 0, 'summary': 'looked'})
    return f'Checked.\n{SELF_CHECK_OPEN}\n{body}\n{SELF_CHECK_CLOSE}'


def test_report(passed: bool) -> str:
    body = json.dumps({'passed': passed, 'command': 'pytest', 'summary': 's',
                       'failures': [] if passed else ['test_login: boom']})
    return f'Ran them.\n{TEST_REPORT_OPEN}\n{body}\n{TEST_REPORT_CLOSE}'


def kind_of(prompt: str) -> str:
    first = prompt.splitlines()[0]
    for marker, kind in (('continue round', 'continue'), ('self-check', 'self_check'),
                         ('run the tests', 'tests'), ('failing tests', 'tests_fix')):
        if marker in first:
            return kind
    return 'fix'


class ResumeChat(FakeChat):
    """Answers each kind of message from ``answers`` (a reply, or
    ``prompt -> reply``). A kind in ``silent`` — or any message ``cut_off``
    picks — is delivered but its turn never ends: the chat was cut off in the
    middle of it."""

    def __init__(self, **answers) -> None:
        self.answers = {'fix': fix_everything, **answers}
        self.silent: set[str] = set()
        self.cut_off = lambda prompt: False
        super().__init__(reply=self._answer)

    def kinds(self) -> list[str]:
        return [kind_of(prompt) for prompt, _ in self.delivered]

    def deliver(self, task_id: str, prompt: str, *, force_respawn: bool) -> bool:
        if kind_of(prompt) in self.silent or self.cut_off(prompt):
            self.delivered.append((prompt, force_respawn))
            return True
        return super().deliver(task_id, prompt, force_respawn=force_respawn)

    def _answer(self, prompt: str) -> str:
        answer = self.answers.get(kind_of(prompt), 'done')
        return answer(prompt) if callable(answer) else answer


class ScriptedReviewer(object):
    """Each review takes the next item: a reply, or an exception to raise.
    The last item repeats."""

    def __init__(self, *items) -> None:
        self.items = list(items)
        self.prompts: list[str] = []

    def review(self, prompt, *, task_id, cancel_event, model=''):
        self.prompts.append(prompt)
        item = self.items.pop(0) if len(self.items) > 1 else self.items[0]
        if isinstance(item, Exception):
            raise item
        return item


MAJOR = reply(finding('MAJOR'))
CLEAN = reply()


class _Resume(unittest.TestCase):

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = ReviewLoopStore(Path(tmp.name))
        self.tree = FakeTree()
        self.events: list[str] = []

    def service(self, chat, reviewer, **kwargs) -> ReviewLoopService:
        service = ReviewLoopService(
            store=self.store, chat=chat, reviewer=reviewer, wording=WORDING,
            observer=lambda state, event: self.events.append(event), options=FAST, **kwargs,
        )
        self.addCleanup(service.shutdown, 'test over')
        return service

    def start(self, service, **options) -> None:
        service.start('T-1', diff_source=self.tree.diff_source, **options)

    def resume(self, service) -> ReviewLoopState:
        return service.resume('T-1', diff_source=self.tree.diff_source)

    def finished(self, service) -> ReviewLoopState:
        self.assertTrue(wait_until(lambda: not service.is_running('T-1')))
        return service.state('T-1')

    def stop_at(self, service, phase: ReviewLoopPhase, round_number: int) -> ReviewLoopState:
        self.assertTrue(wait_until(lambda: (
            service.state('T-1').phase is phase and service.state('T-1').round == round_number
        )))
        service.stop('T-1')
        return self.finished(service)


class AReviewThatFailedTests(_Resume):

    def test_the_round_is_reviewed_again_not_a_new_round(self) -> None:
        # THE REPORT: round 7's review hit its 7200s timeout and "Run again"
        # was the only way on — back to round 1, every decision gone.
        reviewer = ScriptedReviewer(
            MAJOR, RuntimeError('Claude CLI did not finish within 7200s'), CLEAN,
        )
        service = self.service(ResumeChat(), reviewer)
        self.start(service)
        failed = self.finished(service)
        self.assertEqual(failed.status, ReviewLoopStatus.FAILED)
        self.assertEqual(failed.summary()['resume'], "run round 2's review again")

        resumed = self.resume(service)
        self.assertEqual(resumed.status, ReviewLoopStatus.RUNNING)
        state = self.finished(service)
        self.assertEqual(state.status, ReviewLoopStatus.CLEAN)
        self.assertEqual([r.number for r in state.rounds], [1, 2])
        self.assertEqual(state.loop_id, failed.loop_id)
        self.assertEqual((state.resumes, state.resume_note), (1, "run round 2's review again"))
        # The rerun is told round 1's decision, as the first attempt was.
        self.assertIn('R1-1', reviewer.prompts[-1])
        self.assertIn('resumed', self.events)

    def test_a_loop_failed_before_any_round_starts_round_one(self) -> None:
        # The reviewer cannot even start: the loop failed in round 1's review.
        service = self.service(ResumeChat(), ScriptedReviewer(RuntimeError('no CLI'), CLEAN))
        self.start(service)
        self.assertEqual(self.finished(service).summary()['resume'], "run round 1's review again")
        self.resume(service)
        self.assertEqual(self.finished(service).status, ReviewLoopStatus.CLEAN)


class AFixCutOffMidTurnTests(_Resume):

    def test_the_chat_is_nudged_to_continue_and_never_sent_the_findings_twice(self) -> None:
        chat = ResumeChat(**{'continue': respond('R1-1')})
        chat.silent = {'fix'}
        service = self.service(chat, ScriptedReviewer(MAJOR, CLEAN))
        self.start(service)
        stopped = self.stop_at(service, ReviewLoopPhase.AWAITING_FIX, 1)
        self.assertEqual(stopped.summary()['resume'], "nudge the chat to finish round 1's fixes")

        self.resume(service)
        state = self.finished(service)
        self.assertEqual(state.status, ReviewLoopStatus.CLEAN)
        self.assertEqual(chat.kinds(), ['fix', 'continue'])
        nudge = chat.delivered[-1][0]
        self.assertTrue(nudge.startswith('Host review loop — continue round 1\n'))
        self.assertIn(RESPONSE_OPEN, nudge)
        # The nudge's reply is the round's decision ledger.
        self.assertEqual([r.finding_id for r in state.rounds[0].responses], ['R1-1'])
        self.assertEqual(state.rounds[0].outcome, 'sent')
        self.assertGreater(state.rounds[0].fixed_at, 0)
        self.assertIn('nudged', self.events)

    def test_the_resumed_loop_still_counts_the_misses_from_before_the_stop(self) -> None:
        # Round 1 and 2 both find the same issue; the stop comes in round 2's
        # fix. Resumed, round 3 finding it a third time is STUCK — exactly as
        # the uninterrupted loop would have judged it.
        chat = ResumeChat(**{'continue': respond('R2-1')})
        chat.cut_off = lambda prompt: 'round 2 of' in prompt.splitlines()[0]
        service = self.service(chat, ScriptedReviewer(MAJOR))
        self.start(service)
        self.stop_at(service, ReviewLoopPhase.AWAITING_FIX, 2)
        self.resume(service)
        state = self.finished(service)
        self.assertEqual(state.status, ReviewLoopStatus.STUCK)
        self.assertEqual(state.round, 3)
        third = state.rounds[2].findings[0]
        self.assertEqual((third.repeat_of, third.missed_fixes), ('R1-1', 2))

    def test_a_failing_tests_fix_cut_off_is_nudged_too(self) -> None:
        chat = ResumeChat(tests=test_report(False))
        chat.silent = {'tests_fix'}
        service = self.service(chat, ScriptedReviewer(CLEAN))
        self.start(service, verify_tests=True)
        stopped = self.stop_at(service, ReviewLoopPhase.AWAITING_FIX, 1)
        self.assertEqual(
            stopped.summary()['resume'],
            "nudge the chat to finish fixing round 1's failing tests",
        )
        chat.answers['tests'] = test_report(True)
        self.resume(service)
        state = self.finished(service)
        self.assertEqual(state.status, ReviewLoopStatus.CLEAN)
        self.assertEqual(chat.kinds(), ['tests', 'tests_fix', 'continue', 'tests'])
        self.assertIn('fixing the failing tests from round 1', chat.delivered[2][0])
        self.assertEqual(state.rounds[0].responses, [])


class AStuckLoopTests(_Resume):

    def test_the_held_back_findings_are_sent_and_the_loop_goes_on(self) -> None:
        reviewer = ScriptedReviewer(MAJOR, MAJOR, MAJOR, CLEAN)
        chat = ResumeChat()
        service = self.service(chat, reviewer)
        self.start(service)
        stuck = self.finished(service)
        self.assertEqual((stuck.status, stuck.round), (ReviewLoopStatus.STUCK, 3))
        self.assertEqual(stuck.summary()['resume'], "send round 3's findings to the chat")

        self.resume(service)
        state = self.finished(service)
        self.assertEqual(state.status, ReviewLoopStatus.CLEAN)
        self.assertEqual(state.rounds[2].outcome, 'sent')
        self.assertEqual(state.round, 4)
        # Sent the way it was judged: a repeat that survived two fixes.
        self.assertIn('first reported as R1-1', chat.delivered[2][0])

    def test_stuck_on_the_last_round_has_nothing_to_resume(self) -> None:
        service = self.service(ResumeChat(), ScriptedReviewer(MAJOR))
        self.start(service, max_rounds=3)
        stuck = self.finished(service)
        self.assertEqual((stuck.status, stuck.round), (ReviewLoopStatus.STUCK, 3))
        self.assertEqual(stuck.summary()['resume'], '')
        with self.assertRaisesRegex(ReviewLoopError, 'ended stuck — there is nothing to resume'):
            self.resume(service)


class StepsThatNeverWentOutTests(_Resume):

    def test_findings_the_chat_never_got_are_sent(self) -> None:
        chat = ResumeChat()
        reviewer = ScriptedReviewer(MAJOR, CLEAN)
        service = self.service(chat, reviewer)
        busy = ChatReadiness.wait('the agent is mid-turn')
        original_review = reviewer.review

        def review_then_busy(prompt, **kwargs):
            answer = original_review(prompt, **kwargs)
            chat.default_readiness = busy  # the operator's own turn begins
            return answer

        reviewer.review = review_then_busy
        self.start(service)
        stopped = self.stop_at(service, ReviewLoopPhase.WAITING_TO_SEND, 1)
        self.assertEqual(chat.delivered, [])
        self.assertEqual(stopped.summary()['resume'], "send round 1's findings to the chat")

        chat.default_readiness = ChatReadiness.ready()
        reviewer.review = original_review
        self.resume(service)
        state = self.finished(service)
        self.assertEqual(state.status, ReviewLoopStatus.CLEAN)
        self.assertEqual(chat.kinds(), ['fix'])

    def test_failing_tests_the_chat_never_got_are_sent(self) -> None:
        chat = ResumeChat()

        def failing_then_busy(prompt: str) -> str:
            # Free once more — for the loop to read this turn — then the
            # operator's own turn begins before the failures can go out.
            chat.readiness_script = [ChatReadiness.ready()]
            chat.default_readiness = ChatReadiness.wait('the agent is mid-turn')
            return test_report(False)

        chat.answers['tests'] = failing_then_busy
        service = self.service(chat, ScriptedReviewer(CLEAN))
        self.start(service, verify_tests=True)
        stopped = self.stop_at(service, ReviewLoopPhase.WAITING_TO_SEND, 1)
        self.assertEqual(stopped.summary()['resume'], "send round 1's failing tests to the chat")

        chat.default_readiness = ChatReadiness.ready()
        chat.answers['tests'] = test_report(True)
        self.resume(service)
        state = self.finished(service)
        self.assertEqual(state.status, ReviewLoopStatus.CLEAN)
        self.assertEqual(chat.kinds(), ['tests', 'tests_fix', 'tests'])
        self.assertEqual(state.rounds[0].outcome, 'tests_failed')

    def test_a_test_run_cut_off_is_asked_again(self) -> None:
        chat = ResumeChat(tests=test_report(True))
        chat.silent = {'tests'}
        service = self.service(chat, ScriptedReviewer(CLEAN))
        self.start(service, verify_tests=True)
        stopped = self.stop_at(service, ReviewLoopPhase.VERIFYING, 1)
        self.assertEqual(
            stopped.summary()['resume'], "run the tests after round 1's clean review",
        )
        chat.silent = set()
        self.resume(service)
        state = self.finished(service)
        self.assertEqual(state.status, ReviewLoopStatus.CLEAN)
        self.assertEqual(chat.kinds(), ['tests', 'tests'])
        self.assertEqual(state.round, 1)
        self.assertTrue(state.rounds[0].tests.passed)

    def test_a_self_check_cut_off_is_asked_again_as_the_same_turn(self) -> None:
        chat = ResumeChat(self_check=self_check(True))
        chat.silent = {'self_check'}
        service = self.service(chat, ScriptedReviewer(CLEAN))
        self.start(service, self_check=True)
        self.assertTrue(wait_until(lambda: len(chat.delivered) == 1))
        service.stop('T-1')
        stopped = self.finished(service)
        self.assertEqual(stopped.summary()['resume'], 'ask self-check 1 again')

        chat.silent = set()
        self.resume(service)
        state = self.finished(service)
        self.assertEqual(state.status, ReviewLoopStatus.CLEAN)
        self.assertEqual([turn.number for turn in state.self_checks], [1])
        self.assertTrue(state.self_checks[0].clean)
        self.assertEqual(chat.kinds(), ['self_check', 'self_check'])


class AfterARestartTests(_Resume):

    def test_a_loop_the_host_was_shut_down_in_resumes_in_a_new_process(self) -> None:
        chat = ResumeChat(**{'continue': respond('R1-1')})
        chat.silent = {'fix'}
        first = self.service(chat, ScriptedReviewer(MAJOR, CLEAN))
        self.start(first)
        self.assertTrue(wait_until(
            lambda: first.state('T-1').phase is ReviewLoopPhase.AWAITING_FIX,
        ))
        first.shutdown('host shutting down')
        self.assertEqual(self.finished(first).status, ReviewLoopStatus.INTERRUPTED)

        # The next process knows the loop only from disk.
        second = self.service(chat, ScriptedReviewer(CLEAN))
        self.resume(second)
        state = self.finished(second)
        self.assertEqual(state.status, ReviewLoopStatus.CLEAN)
        self.assertEqual(chat.kinds(), ['fix', 'continue'])

    def test_a_round_that_was_done_when_the_loop_broke_begins_the_next(self) -> None:
        # Saved by a loop that broke between rounds: round 1 fully fixed.
        state = _state(ReviewLoopStatus.FAILED, _round(1, outcome='sent', fixed=True))
        self.store.save(state)
        reviewer = ScriptedReviewer(CLEAN)
        service = self.service(ResumeChat(), reviewer)
        self.resume(service)
        done = self.finished(service)
        self.assertEqual(done.status, ReviewLoopStatus.CLEAN)
        self.assertEqual([r.number for r in done.rounds], [1, 2])
        self.assertIn('R1-1', reviewer.prompts[0])  # told round 1's decision

    def test_a_clean_round_that_broke_before_concluding_concludes(self) -> None:
        clean = _round(1, outcome='failed', clean=True)
        clean.diff_digest = candidate_digest(self.tree.diff_source('T-1'))  # what it reviewed
        self.store.save(_state(ReviewLoopStatus.FAILED, clean))
        reviewer = ScriptedReviewer(CLEAN)
        service = self.service(ResumeChat(), reviewer)
        self.resume(service)
        done = self.finished(service)
        self.assertEqual(done.status, ReviewLoopStatus.CLEAN)
        self.assertEqual(reviewer.prompts, [])  # its review was already done


class ResumeRefusalTests(_Resume):

    def test_no_loop_yet(self) -> None:
        service = self.service(ResumeChat(), ScriptedReviewer(CLEAN))
        with self.assertRaisesRegex(ReviewLoopError, 'no review loop to resume'):
            self.resume(service)

    def test_a_clean_loop_is_done(self) -> None:
        service = self.service(ResumeChat(), ScriptedReviewer(CLEAN))
        self.start(service)
        self.finished(service)
        with self.assertRaisesRegex(ReviewLoopError, 'ended clean'):
            self.resume(service)

    def test_not_while_one_runs(self) -> None:
        chat = ResumeChat()
        chat.silent = {'fix'}
        service = self.service(chat, ScriptedReviewer(MAJOR))
        self.start(service)
        with self.assertRaisesRegex(ReviewLoopError, 'already running'):
            self.resume(service)

    def test_the_host_can_refuse(self) -> None:
        service = self.service(
            ResumeChat(), ScriptedReviewer(CLEAN), can_start=lambda task_id: 'Plan mode',
        )
        with self.assertRaisesRegex(ReviewLoopError, 'Plan mode'):
            self.resume(service)

    def test_a_bad_task_id(self) -> None:
        service = self.service(ResumeChat(), ScriptedReviewer(CLEAN))
        with self.assertRaisesRegex(ReviewLoopError, 'not a task id'):
            service.resume('../x', diff_source=self.tree.diff_source)


# ----- the state alone: where a saved loop can pick up -----

def _finding_of(severity: str = 'MAJOR', number: int = 1) -> ReviewFinding:
    return ReviewFinding(
        severity=FindingSeverity(severity), title='a bug', file='app.py',
        repository='api', symbol='run', id=f'R{number}-1',
    )


def _round(
    number: int, *, outcome: str = '', reviewed: bool = True, clean: bool = False,
    fixed: bool = False, tests: TestReport | None = None,
) -> ReviewRound:
    review_round = ReviewRound(number=number, started_at=10.0, outcome=outcome)
    if reviewed:
        review_round.reviewed_at = 11.0
        review_round.findings = [] if clean else [_finding_of(number=number)]
    if fixed:
        review_round.sent_at = 12.0
        review_round.fixed_at = 13.0
        review_round.finished_at = 13.0
        review_round.responses = [
            _response(review_round.findings[0]),
        ] if review_round.findings else []
    review_round.tests = tests
    return review_round


def _response(found: ReviewFinding) -> FindingResponse:
    return FindingResponse(found.id, found.fingerprint, FindingDecision.FIXED, 'x', 't')


def _state(status: ReviewLoopStatus, *rounds: ReviewRound, max_rounds: int = 5,
           **options) -> ReviewLoopState:
    state = ReviewLoopState.new('T-1', max_rounds=max_rounds, now=1.0, **options)
    state.status = status
    state.phase = ReviewLoopPhase.DONE
    state.rounds = list(rounds)
    return state


class ResumePointTests(unittest.TestCase):

    def point(self, state: ReviewLoopState) -> tuple | None:
        found = state.resume_point()
        return None if found is None else (found.stage, found.number, found.description)

    def test_finished_and_running_loops_have_none(self) -> None:
        for status in (ReviewLoopStatus.RUNNING, ReviewLoopStatus.CLEAN, ReviewLoopStatus.MAX_ROUNDS):
            with self.subTest(status=status):
                self.assertIsNone(_state(status, _round(1, reviewed=False)).resume_point())

    def test_before_any_round(self) -> None:
        stopped = ReviewLoopStatus.STOPPED
        self.assertEqual(self.point(_state(stopped)), (ResumeStage.REVIEW, 1, 'start round 1'))
        cut = _state(stopped)
        cut.self_checks = [SelfCheckTurn(1, 1.0, finished_at=2.0, clean=False),
                           SelfCheckTurn(2, 3.0)]
        self.assertEqual(self.point(cut), (ResumeStage.SELF_CHECK, 2, 'ask self-check 2 again'))
        done = _state(stopped)
        done.self_checks = [SelfCheckTurn(1, 1.0, finished_at=2.0, clean=True)]
        self.assertEqual(self.point(done), (ResumeStage.REVIEW, 1, 'start round 1'))

    def test_each_place_a_round_can_be_cut_off(self) -> None:
        failed = ReviewLoopStatus.FAILED
        tests_failed = TestReport(passed=False, failures=['x'])
        cases = [
            (_round(3, outcome='failed', reviewed=False), (ResumeStage.REVIEW, 3)),
            (_round(3, outcome='sent'), (ResumeStage.CONTINUE_FIX, 3)),
            (_round(3, outcome='sent', fixed=True), (ResumeStage.REVIEW, 4)),
            (_round(3, outcome='tests_failed', clean=True, tests=tests_failed),
             (ResumeStage.CONTINUE_TESTS_FIX, 3)),
            (_round(3, outcome='tests_failed', clean=True, tests=tests_failed, fixed=True),
             (ResumeStage.REVIEW, 4)),
            (_round(3, outcome='clean', clean=True), (ResumeStage.REVIEW, 4)),
            (_round(3, outcome='changed', clean=True), (ResumeStage.REVIEW, 4)),
            (_round(3, outcome='stopped'), (ResumeStage.SEND_FINDINGS, 3)),
            (_round(3, outcome='stuck'), (ResumeStage.SEND_FINDINGS, 3)),
            (_round(3, outcome='stopped', clean=True, tests=tests_failed),
             (ResumeStage.SEND_TESTS_FIX, 3)),
            (_round(3, outcome='stopped', clean=True), (ResumeStage.FINISH_CLEAN, 3)),
        ]
        for review_round, expected in cases:
            with self.subTest(outcome=review_round.outcome, expected=expected):
                point = _state(failed, review_round).resume_point()
                self.assertEqual((point.stage, point.number), expected)

    def test_a_clean_round_says_whether_the_tests_still_have_to_run(self) -> None:
        untested = _state(ReviewLoopStatus.STOPPED, _round(2, outcome='stopped', clean=True),
                          verify_tests=True)
        self.assertEqual(untested.resume_point().description,
                         "run the tests after round 2's clean review")
        tested = _state(ReviewLoopStatus.STOPPED, _round(
            2, outcome='stopped', clean=True, tests=TestReport(passed=True),
        ), verify_tests=True)
        self.assertEqual(tested.resume_point().description, "finish round 2's clean review")

    def test_nothing_is_sent_after_the_last_round(self) -> None:
        last = [
            _round(5, outcome='stuck'),
            _round(5, outcome='stopped', clean=True, tests=TestReport(passed=False)),
            _round(5, outcome='sent', fixed=True),
            _round(5, outcome='max_rounds'),
        ]
        for review_round in last:
            with self.subTest(outcome=review_round.outcome):
                self.assertIsNone(_state(ReviewLoopStatus.STUCK, review_round).resume_point())


class ReopenTests(unittest.TestCase):

    def test_a_review_run_again_starts_its_round_over(self) -> None:
        state = _state(ReviewLoopStatus.FAILED, _round(1, outcome='sent', fixed=True),
                       _round(2, outcome='failed', reviewed=False))
        state.reason = 'Claude CLI did not finish'
        state.finished_at = 20.0
        state.reopen(state.resume_point(), now=30.0)
        self.assertEqual(state.status, ReviewLoopStatus.RUNNING)
        self.assertEqual(state.phase, ReviewLoopPhase.WAITING_TO_REVIEW)
        self.assertEqual((state.reason, state.finished_at, state.phase_started_at), ('', 0.0, 30.0))
        self.assertEqual(state.rounds[1], ReviewRound(number=2, started_at=30.0))
        self.assertEqual(state.rounds[0].outcome, 'sent')  # earlier rounds stay

    def test_a_new_round_leaves_the_rounds_alone(self) -> None:
        state = _state(ReviewLoopStatus.FAILED, _round(1, outcome='clean', clean=True))
        before = [r.to_dict() for r in state.rounds]
        state.reopen(state.resume_point(), now=30.0)
        self.assertEqual([r.to_dict() for r in state.rounds], before)

    def test_a_nudged_fix_keeps_its_outcome_and_a_resend_clears_it(self) -> None:
        nudged = _state(ReviewLoopStatus.STOPPED, _round(1, outcome='sent'))
        nudged.rounds[0].finished_at = 15.0
        nudged.reopen(nudged.resume_point(), now=30.0)
        self.assertEqual((nudged.rounds[0].outcome, nudged.rounds[0].finished_at), ('sent', 0.0))
        self.assertEqual(nudged.phase, ReviewLoopPhase.WAITING_TO_SEND)
        resent = _state(ReviewLoopStatus.STUCK, _round(1, outcome='stuck'))
        resent.reopen(resent.resume_point(), now=30.0)
        self.assertEqual(resent.rounds[0].outcome, '')

    def test_a_self_check_drops_the_cut_off_turn(self) -> None:
        state = _state(ReviewLoopStatus.STOPPED)
        state.self_checks = [SelfCheckTurn(1, 1.0, finished_at=2.0), SelfCheckTurn(2, 3.0)]
        state.reopen(state.resume_point(), now=30.0)
        self.assertEqual([turn.number for turn in state.self_checks], [1])
        self.assertEqual(state.phase, ReviewLoopPhase.SELF_CHECK)

    def test_resumes_are_counted_and_round_trip(self) -> None:
        state = _state(ReviewLoopStatus.STOPPED, _round(1, outcome='sent'))
        state.reopen(ResumePoint(ResumeStage.CONTINUE_FIX, 1, 'nudge it'), now=30.0)
        state.reopen(ResumePoint(ResumeStage.CONTINUE_FIX, 1, 'nudge it again'), now=31.0)
        copy = ReviewLoopState.from_dict(state.to_dict())
        self.assertEqual((copy.resumes, copy.resume_note), (2, 'nudge it again'))
        self.assertEqual(state.summary()['resumes'], 2)
        self.assertEqual(state.summary()['resume'], '')  # running: nothing to resume

    def test_a_loop_saved_before_resume_existed_reads_as_never_resumed(self) -> None:
        data = _state(ReviewLoopStatus.FAILED, _round(1, reviewed=False)).to_dict()
        for key in ('resumes', 'resume_note', 'resume'):
            data.pop(key)
        old = ReviewLoopState.from_dict(data)
        self.assertEqual((old.resumes, old.resume_note), (0, ''))
        self.assertEqual(old.summary()['resume'], "run round 1's review again")


class LoopMemoryTests(unittest.TestCase):
    """The between-rounds memory, read back from saved rounds."""

    def test_nothing_before_the_first_round(self) -> None:
        memory = loop_memory([])
        self.assertEqual((memory.previous, memory.missed, memory.first_ids,
                          memory.confirm_next, memory.tests_digest),
                         (None, {}, {}, False, ''))

    def test_a_fix_claim_then_the_same_issue_is_one_missed_fix(self) -> None:
        first, second = _round(1, outcome='sent', fixed=True), _round(2, outcome='sent', fixed=True)
        fingerprint = first.findings[0].fingerprint
        memory = loop_memory([first, second])
        self.assertEqual(memory.missed, {fingerprint: 1})
        self.assertEqual(memory.first_ids, {fingerprint: 'R1-1'})
        self.assertEqual(memory.previous, frozenset({fingerprint}))

    def test_a_clean_round_clears_the_claims_and_asks_for_a_sweep(self) -> None:
        clean = _round(2, outcome='clean', clean=True, tests=TestReport(passed=True))
        clean.diff_digest = 'abc'
        memory = loop_memory([_round(1, outcome='sent', fixed=True), clean])
        self.assertIsNone(memory.previous)
        self.assertTrue(memory.confirm_next)
        self.assertEqual(memory.tests_digest, 'abc')

    def test_a_changed_tree_is_reviewed_the_way_it_was(self) -> None:
        sweep = _round(1, outcome='changed', clean=True)
        sweep.sweep = True
        self.assertTrue(loop_memory([sweep]).confirm_next)
        self.assertFalse(loop_memory([_round(1, outcome='changed', clean=True)]).confirm_next)

    def test_failing_tests_are_not_a_pass(self) -> None:
        failing = _round(1, outcome='tests_failed', clean=True, tests=TestReport(passed=False))
        failing.diff_digest = 'abc'
        memory = loop_memory([failing])
        self.assertEqual((memory.tests_digest, memory.confirm_next), ('', False))


class ContinuePromptTests(unittest.TestCase):

    def test_a_findings_nudge_asks_for_the_decisions(self) -> None:
        prompt = build_continue_prompt(round_number=4, failing_tests=False, wording=WORDING)
        self.assertTrue(prompt.startswith('Host review loop — continue round 4\n'))
        self.assertIn("round 4's findings", prompt)
        self.assertIn(RESPONSE_OPEN, prompt)
        self.assertIn('Never print the done marker.', prompt)

    def test_a_tests_nudge_asks_for_the_tests_again(self) -> None:
        prompt = build_continue_prompt(round_number=2, failing_tests=True, wording=WORDING)
        self.assertIn('fixing the failing tests from round 2', prompt)
        self.assertIn('Run the tests again', prompt)
        self.assertNotIn(RESPONSE_OPEN, prompt)


if __name__ == '__main__':
    unittest.main()
