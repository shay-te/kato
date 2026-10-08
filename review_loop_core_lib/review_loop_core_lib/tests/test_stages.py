"""The loop's three optional stages, end to end through the public service.

* **self-check** — before any independent review, the main chat reviews and
  fixes its own change (cheap: it holds the whole context);
* **verify_tests** — a clean review only ends the loop once the main chat ran
  the tests and they pass; failures go back to be fixed;
* **confirm_clean** — a clean verdict from a reviewer that was told the fix
  ping-pong needs a clean-room reviewer (told nothing) to agree.

Real service + runner; only the chat and the reviewer are stand-ins
(fakes.py). The chat answers each kind of message with a scripted reply.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from review_loop_core_lib.review_loop_core_lib.chat_prompts import (
    SELF_CHECK_CLOSE,
    SELF_CHECK_OPEN,
    TEST_REPORT_CLOSE,
    TEST_REPORT_OPEN,
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
from review_loop_core_lib.review_loop_core_lib.ports import LoopWording
from review_loop_core_lib.review_loop_core_lib.response import (
    parse_self_check,
    parse_test_report,
)
from review_loop_core_lib.review_loop_core_lib.runner import _tests_note
from review_loop_core_lib.review_loop_core_lib.service import ReviewLoopService
from review_loop_core_lib.review_loop_core_lib.store import ArtifactKind, ReviewLoopStore
from review_loop_core_lib.review_loop_core_lib.tests.fakes import (
    FAST,
    WORDING,
    FakeChat,
    FakeReviewer,
    FakeTree,
    finding,
    reply,
    wait_until,
    wrap,
)


def self_check(clean: bool, fixed: int = 0, summary: str = '') -> str:
    body = json.dumps({'clean': clean, 'fixed': fixed, 'summary': summary})
    return f'Checked it.\n{SELF_CHECK_OPEN}\n{body}\n{SELF_CHECK_CLOSE}'


def test_report(passed: bool, *, failures=(), summary: str = '', command: str = 'pytest') -> str:
    body = json.dumps({'passed': passed, 'command': command, 'summary': summary,
                       'failures': list(failures)})
    return f'Ran them.\n{TEST_REPORT_OPEN}\n{body}\n{TEST_REPORT_CLOSE}'


def kind_of(prompt: str) -> str:
    first = prompt.splitlines()[0]
    if 'self-check' in first:
        return 'own'
    if 'run the tests' in first:
        return 'tests'
    if 'failing tests' in first:
        return 'tests_fix'
    return 'fix'


class ScriptedChat(FakeChat):
    """A chat that answers each kind of message from its own script.

    The last reply of each script repeats; an empty script answers 'done'.
    """

    def __init__(self, **scripts) -> None:
        self.scripts = {key: list(value) for key, value in scripts.items()}
        self.kinds: list[str] = []
        super().__init__(reply=self._answer)

    def _answer(self, prompt: str) -> str:
        kind = kind_of(prompt)
        self.kinds.append(kind)
        queue = self.scripts.get(kind) or []
        if not queue:
            return 'done'
        return queue.pop(0) if len(queue) > 1 else queue[0]


class _Stages(unittest.TestCase):

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = ReviewLoopStore(Path(tmp.name))
        self.tree = FakeTree()
        self.events: list[str] = []

    def run_loop(self, chat, reviewer, *, options=FAST, max_rounds=5, **stages) -> ReviewLoopState:
        service = ReviewLoopService(
            store=self.store, chat=chat, reviewer=reviewer, wording=WORDING,
            observer=lambda state, event: self.events.append(event), options=options,
        )
        self.addCleanup(service.shutdown, 'test over')
        self.service = service
        service.start('T-1', diff_source=self.tree.diff_source, max_rounds=max_rounds, **stages)
        self.assertTrue(wait_until(lambda: not service.is_running('T-1')))
        return service.state('T-1')


class SelfCheckTests(_Stages):

    def test_the_chat_checks_itself_first_and_stops_when_it_says_clean(self) -> None:
        chat = ScriptedChat(own=[self_check(False, 2, 'fixed two'), self_check(True)])
        reviewer = FakeReviewer(reply())
        state = self.run_loop(chat, reviewer, self_check=True)

        self.assertEqual(state.status, ReviewLoopStatus.CLEAN, state.reason)
        self.assertEqual(chat.kinds, ['own', 'own'])  # then a clean review: nothing sent
        self.assertEqual([(t.number, t.clean, t.fixed) for t in state.self_checks],
                         [(1, False, 2), (2, True, 0)])
        self.assertEqual(state.self_checks[0].summary, 'fixed two')
        first = chat.delivered[0][0]
        self.assertEqual(first.splitlines()[0], 'Host review loop — self-check 1 of 3')
        self.assertIn('1. Correctness', first)          # the shared checklist
        self.assertTrue(first.rstrip().endswith('Never print the done marker.'))
        self.assertIn('self-checked', ' '.join(self.events).replace('_', '-'))
        reply_text = self.service.artifact('T-1', state.loop_id, 1, ArtifactKind.SELF_CHECK)
        self.assertIn(SELF_CHECK_OPEN, reply_text)
        # The independent review ran only after the self-check.
        self.assertEqual(len(reviewer.prompts), 1)

    def test_it_gives_up_after_the_turn_limit_and_reviews_anyway(self) -> None:
        chat = ScriptedChat(own=[self_check(False, 1)])
        state = self.run_loop(chat, FakeReviewer(reply()), self_check=True,
                              options=replace(FAST, self_check_turns=2))
        self.assertEqual(len(state.self_checks), 2)
        self.assertEqual(state.status, ReviewLoopStatus.CLEAN, state.reason)

    def test_a_reply_without_a_block_ends_the_self_check(self) -> None:
        chat = ScriptedChat(own=['I looked, seems fine.'])
        state = self.run_loop(chat, FakeReviewer(reply()), self_check=True)
        self.assertEqual([t.clean for t in state.self_checks], [None])

    def test_an_empty_reply_keeps_no_artifact(self) -> None:
        chat = ScriptedChat(own=[''])
        state = self.run_loop(chat, FakeReviewer(reply()), self_check=True)
        self.assertIsNone(self.service.artifact('T-1', state.loop_id, 1, ArtifactKind.SELF_CHECK))

    def test_a_self_check_turn_that_errors_fails_the_loop_with_its_name(self) -> None:
        chat = ScriptedChat(own=[self_check(True)])
        chat.turn_error = True
        state = self.run_loop(chat, FakeReviewer(reply()), self_check=True)
        self.assertEqual(state.status, ReviewLoopStatus.FAILED)
        self.assertEqual(state.reason, "the chat's self-check turn ended with an error")


class TestsGateTests(_Stages):

    def test_clean_needs_the_tests_to_pass(self) -> None:
        chat = ScriptedChat(tests=[test_report(True, summary='41 passed')])
        state = self.run_loop(chat, FakeReviewer(reply()), verify_tests=True)
        self.assertEqual(state.status, ReviewLoopStatus.CLEAN, state.reason)
        self.assertEqual(state.reason, 'round 1 found no blocking issues; tests passed (41 passed)')
        self.assertEqual(chat.kinds, ['tests'])
        self.assertTrue(state.rounds[0].tests.passed)
        first = chat.delivered[0][0]
        self.assertEqual(first.splitlines()[0], 'Host review loop — run the tests')
        self.assertIn('Do NOT change code', first)
        self.assertIn('41 passed',
                      self.service.artifact('T-1', state.loop_id, 1, ArtifactKind.TESTS))

    def test_failing_tests_go_back_to_be_fixed_then_the_loop_goes_on(self) -> None:
        chat = ScriptedChat(tests=[
            test_report(False, failures=['test_divide: ZeroDivisionError']),
            test_report(True, summary='all green'),
        ])
        state = self.run_loop(chat, FakeReviewer(reply()), verify_tests=True)

        self.assertEqual(state.status, ReviewLoopStatus.CLEAN, state.reason)
        self.assertEqual(chat.kinds, ['tests', 'tests_fix', 'tests'])
        self.assertEqual([r.outcome for r in state.rounds], ['tests_failed', 'clean'])
        fix = chat.delivered[1][0]
        self.assertEqual(fix.splitlines()[0], 'Host review loop — fix the failing tests')
        self.assertIn(wrap('- test_divide: ZeroDivisionError', 'task T-1 failing tests'), fix)
        self.assertEqual(state.rounds[0].tests.failures, ['test_divide: ZeroDivisionError'])
        self.assertTrue(state.rounds[0].sent_at and state.rounds[0].fixed_at)

    def test_tests_still_failing_on_the_last_round_is_the_round_limit(self) -> None:
        chat = ScriptedChat(tests=[test_report(False, summary='2 failed')])
        state = self.run_loop(chat, FakeReviewer(reply()), verify_tests=True, max_rounds=1)
        self.assertEqual(state.status, ReviewLoopStatus.MAX_ROUNDS)
        self.assertEqual(state.reason, 'the tests still fail after 1 reviews')
        self.assertEqual(state.rounds[0].outcome, 'tests_failed')

    def test_a_failure_with_no_details_sends_the_summary(self) -> None:
        chat = ScriptedChat(tests=[test_report(False, summary='suite crashed'), test_report(True)])
        self.run_loop(chat, FakeReviewer(reply()), verify_tests=True)
        self.assertIn('- suite crashed', chat.delivered[1][0])

    def test_an_unreported_run_does_not_block_but_says_so(self) -> None:
        chat = ScriptedChat(tests=['ran them, all good I think'])
        state = self.run_loop(chat, FakeReviewer(reply()), verify_tests=True)
        self.assertEqual(state.status, ReviewLoopStatus.CLEAN)
        self.assertEqual(state.reason, 'round 1 found no blocking issues; the tests were not reported')
        self.assertIsNone(state.rounds[0].tests.passed)

    def test_an_empty_test_reply_keeps_no_artifact(self) -> None:
        chat = ScriptedChat(tests=[''])
        state = self.run_loop(chat, FakeReviewer(reply()), verify_tests=True)
        self.assertIsNone(self.service.artifact('T-1', state.loop_id, 1, ArtifactKind.TESTS))


class CleanRoomTests(_Stages):

    def test_a_ledger_informed_clean_is_confirmed_by_a_clean_room_review(self) -> None:
        chat = ScriptedChat()
        # R1 blocking → fixed; R2 (told the ledger) clean; R3 clean-room clean.
        reviewer = FakeReviewer(reply(finding('MAJOR')), reply(), reply())
        state = self.run_loop(chat, reviewer, confirm_clean=True)

        self.assertEqual(state.status, ReviewLoopStatus.CLEAN, state.reason)
        self.assertEqual([r.outcome for r in state.rounds], ['sent', 'clean', 'clean'])
        self.assertEqual([r.blind for r in state.rounds], [True, False, True])
        self.assertEqual(state.reason,
                         'round 3 found no blocking issues; confirmed by a clean-room review')
        self.assertIn('## Decisions so far', reviewer.prompts[1])
        self.assertNotIn('## Decisions so far', reviewer.prompts[2])

    def test_a_clean_first_review_needs_no_confirmation(self) -> None:
        state = self.run_loop(ScriptedChat(), FakeReviewer(reply()), confirm_clean=True)
        self.assertEqual(len(state.rounds), 1)
        self.assertEqual(state.reason, 'round 1 found no blocking issues')

    def test_the_clean_room_reviewer_finding_more_sends_it_back(self) -> None:
        reviewer = FakeReviewer(
            reply(finding('MAJOR')), reply(),             # R1 fix, R2 informed clean
            reply(finding('MAJOR', symbol='other')),      # R3 clean-room: new issue
            reply(), reply(),                             # R4 informed clean, R5 clean-room
        )
        state = self.run_loop(ScriptedChat(), reviewer, confirm_clean=True)
        self.assertEqual(state.status, ReviewLoopStatus.CLEAN, state.reason)
        self.assertEqual([r.outcome for r in state.rounds],
                         ['sent', 'clean', 'sent', 'clean', 'clean'])

    def test_no_round_left_for_the_check_is_said_plainly(self) -> None:
        reviewer = FakeReviewer(reply(finding('MAJOR')), reply())
        state = self.run_loop(ScriptedChat(), reviewer, confirm_clean=True, max_rounds=2)
        self.assertEqual(state.status, ReviewLoopStatus.CLEAN)
        self.assertEqual(state.reason,
                         'round 2 found no blocking issues; no round left for a clean-room review')


class ExtraSweepTests(_Stages):
    """"Clean, then run it again — and boom, a MAJOR." One clean review is not
    proof: ``extra_sweep`` makes every clean verdict face one more fresh,
    clean-room reviewer before the loop believes it."""

    def test_a_clean_first_review_still_gets_a_second_sweep(self) -> None:
        reviewer = FakeReviewer(reply(), reply())
        state = self.run_loop(ScriptedChat(), reviewer, extra_sweep=True)
        self.assertEqual(state.status, ReviewLoopStatus.CLEAN, state.reason)
        self.assertEqual([r.blind for r in state.rounds], [True, True])
        self.assertEqual([r.sweep for r in state.rounds], [False, True])
        self.assertTrue(state.summary()['sweep'])
        self.assertEqual(state.reason,
                         'round 2 found no blocking issues; confirmed by a clean-room review')

    def test_the_sweep_catching_a_major_sends_it_back_and_sweeps_again(self) -> None:
        chat = ScriptedChat()
        reviewer = FakeReviewer(
            reply(),                       # R1 clean
            reply(finding('MAJOR')),       # R2 sweep: boom, a MAJOR
            reply(),                       # R3 (told the fix) clean
            reply(),                       # R4 sweep: clean
        )
        state = self.run_loop(chat, reviewer, extra_sweep=True)
        self.assertEqual(state.status, ReviewLoopStatus.CLEAN, state.reason)
        self.assertEqual([r.outcome for r in state.rounds], ['clean', 'sent', 'clean', 'clean'])
        self.assertEqual(chat.kinds, ['fix'])
        self.assertNotIn('## Decisions so far', reviewer.prompts[3])

    def test_the_sweep_needs_a_round_to_run_in(self) -> None:
        state = self.run_loop(ScriptedChat(), FakeReviewer(reply()), extra_sweep=True,
                              max_rounds=1)
        self.assertEqual(state.status, ReviewLoopStatus.CLEAN)
        self.assertEqual(state.reason,
                         'round 1 found no blocking issues; no round left for a clean-room review')

    def test_the_option_round_trips(self) -> None:
        state = ReviewLoopState.new('T-1', max_rounds=2, now=0.0, extra_sweep=True)
        again = ReviewLoopState.from_dict(state.to_dict())
        self.assertTrue(again.extra_sweep)
        self.assertTrue(again.summary()['extra_sweep'])


class AllStagesTests(_Stages):

    def test_the_full_path_self_check_review_fix_tests_clean_room(self) -> None:
        chat = ScriptedChat(own=[self_check(True)], tests=[test_report(True, summary='ok')])
        reviewer = FakeReviewer(reply(finding('MAJOR')), reply(), reply())
        state = self.run_loop(chat, reviewer, self_check=True, verify_tests=True,
                              confirm_clean=True)
        self.assertEqual(state.status, ReviewLoopStatus.CLEAN, state.reason)
        # The tests ran ONCE: the clean-room review changed nothing, so they
        # are not run again on the same tree.
        self.assertEqual(chat.kinds, ['own', 'fix', 'tests'])
        self.assertEqual(state.reason, 'round 3 found no blocking issues; '
                         'confirmed by a clean-room review; tests passed (ok)')

    def run_sweep_catching_a_major(self, chat) -> ReviewLoopState:
        """R1 clean → tests → sweep R2 finds a MAJOR → fix → R3 clean → sweep R4."""
        reviewer = FakeReviewer(reply(), reply(finding('MAJOR', symbol='x')), reply(), reply())
        state = self.run_loop(chat, reviewer, verify_tests=True, extra_sweep=True)
        self.assertEqual(state.status, ReviewLoopStatus.CLEAN, state.reason)
        self.assertEqual([r.outcome for r in state.rounds], ['clean', 'sent', 'clean', 'clean'])
        return state

    def test_tests_rerun_after_a_fix_changes_the_tree(self) -> None:
        chat = ScriptedChat(tests=[test_report(True, summary='ok')])
        chat.fixer = lambda prompt: self.tree.files.update({'app.py': 'print(2)\n'})
        self.run_sweep_catching_a_major(chat)
        # Once on R1's code, once on the fixed code; not again on R4 (same code).
        self.assertEqual(chat.kinds, ['tests', 'fix', 'tests'])

    def test_tests_are_not_rerun_when_the_fix_changed_nothing(self) -> None:
        chat = ScriptedChat(tests=[test_report(True, summary='ok')])
        self.run_sweep_catching_a_major(chat)
        # The fix turn rejected or skipped everything: the tests already
        # passed on exactly this code.
        self.assertEqual(chat.kinds, ['tests', 'fix'])


class EditingReviewer(FakeReviewer):
    """Someone changes the code while the listed reviews (1-based) run."""

    def __init__(self, tree: FakeTree, *replies: str, edit_during=(1,)) -> None:
        super().__init__(*replies)
        self.tree = tree
        self.edit_during = set(edit_during)

    def review(self, prompt, *, task_id, cancel_event, model='') -> str:
        number = len(self.prompts) + 1
        if number in self.edit_during:
            self.tree.files['app.py'] = f'print({number + 100})\n'
        return super().review(prompt, task_id=task_id, cancel_event=cancel_event, model=model)


class CandidateIdentityTests(_Stages):
    """A clean verdict covers the code that reviewer saw — nothing else."""

    def test_a_clean_review_of_code_that_changed_since_is_reviewed_again(self) -> None:
        reviewer = EditingReviewer(self.tree, reply())
        state = self.run_loop(FakeChat(), reviewer)

        self.assertEqual(state.status, ReviewLoopStatus.CLEAN, state.reason)
        self.assertEqual([r.outcome for r in state.rounds], ['changed', 'clean'])
        first, second = state.rounds
        self.assertNotEqual(first.diff_digest, second.diff_digest)
        self.assertIn('print(101)', reviewer.prompts[1])   # round 2 saw the new code
        self.assertIn('changed', self.events)
        self.assertEqual(state.reason, 'round 2 found no blocking issues')

    def test_a_change_after_the_last_round_is_the_round_limit_not_clean(self) -> None:
        state = self.run_loop(FakeChat(), EditingReviewer(self.tree, reply()), max_rounds=1)
        self.assertEqual(state.status, ReviewLoopStatus.MAX_ROUNDS)
        self.assertEqual(state.rounds[0].outcome, 'changed')
        self.assertEqual(state.reason, 'the code changed after round 1 found it clean, '
                         'and no round was left to review it again')

    def test_a_sweep_whose_code_changed_is_redone_as_a_sweep(self) -> None:
        reviewer = EditingReviewer(self.tree, reply(), edit_during=(2,))
        state = self.run_loop(FakeChat(), reviewer, extra_sweep=True)
        self.assertEqual(state.status, ReviewLoopStatus.CLEAN, state.reason)
        self.assertEqual([(r.outcome, r.sweep) for r in state.rounds],
                         [('clean', False), ('changed', True), ('clean', True)])
        self.assertIn('confirmed by a clean-room review', state.reason)

    def test_the_tests_turn_changing_code_gets_it_reviewed_and_tested_again(self) -> None:
        chat = ScriptedChat(tests=[test_report(True, summary='ok')])
        edits = iter(['print(7)\n'])

        def tests_turn_edits_once(prompt: str) -> None:
            new = next(edits, None)
            if new is not None:
                self.tree.files['app.py'] = new
        chat.fixer = tests_turn_edits_once
        state = self.run_loop(chat, FakeReviewer(reply()), verify_tests=True)

        self.assertEqual(state.status, ReviewLoopStatus.CLEAN, state.reason)
        self.assertEqual([r.outcome for r in state.rounds], ['changed', 'clean'])
        self.assertEqual(chat.kinds, ['tests', 'tests'])

    def test_an_unchanged_tree_is_accepted_without_another_round(self) -> None:
        state = self.run_loop(FakeChat(), FakeReviewer(reply()))
        self.assertEqual([r.outcome for r in state.rounds], ['clean'])
        self.assertTrue(state.rounds[0].diff_digest)
        # Read once for the review, once to confirm it is still that code.
        self.assertEqual(self.tree.calls, 2)


class RoundTimingTests(_Stages):
    """Every round records when it ended, so the view can say how long it took."""

    def assert_in_order(self, *stamps: float) -> None:
        self.assertGreater(stamps[0], 0)
        self.assertEqual(list(stamps), sorted(stamps))

    def test_a_clean_round_ends_after_its_review_and_its_tests(self) -> None:
        chat = ScriptedChat(tests=[test_report(True)])
        state = self.run_loop(chat, FakeReviewer(reply()), verify_tests=True)
        only = state.rounds[0]
        self.assert_in_order(only.started_at, only.reviewed_at, only.finished_at)
        # The fake chat stamps a turn 1ms ahead (fakes.FakeChat), so the test
        # report can read up to that much after the round's end.
        self.assertGreaterEqual(only.finished_at, only.tests.reported_at - 0.001)

    def test_a_sent_round_ends_when_its_fix_turn_does(self) -> None:
        state = self.run_loop(FakeChat(), FakeReviewer(reply(finding('MAJOR')), reply()))
        sent, clean = state.rounds
        self.assertEqual((sent.outcome, clean.outcome), ('sent', 'clean'))
        self.assert_in_order(sent.started_at, sent.sent_at, sent.finished_at, clean.started_at,
                             clean.finished_at)

    def test_a_round_closed_mid_loop_ends_before_the_next_starts(self) -> None:
        # A clean review followed by its clean-room sweep: the first round is
        # closed while the loop goes on, so the loop's own finish never stamps it.
        state = self.run_loop(FakeChat(), FakeReviewer(reply()), extra_sweep=True)
        first, sweep = state.rounds
        self.assertEqual((first.outcome, sweep.sweep), ('clean', True))
        self.assert_in_order(first.started_at, first.reviewed_at, first.finished_at,
                             sweep.started_at, sweep.finished_at)

    def test_a_failing_tests_round_ends_after_their_fix(self) -> None:
        chat = ScriptedChat(tests=[test_report(False, failures=['t1']), test_report(True)])
        state = self.run_loop(chat, FakeReviewer(reply()), verify_tests=True)
        failed, clean = state.rounds
        self.assertEqual(failed.outcome, 'tests_failed')
        self.assert_in_order(failed.started_at, failed.sent_at, failed.finished_at, clean.started_at)

    def test_a_round_saved_before_this_field_reads_as_unfinished(self) -> None:
        self.assertEqual(ReviewRound.from_dict({'number': 1}).finished_at, 0.0)
        stamped = ReviewRound(number=1, started_at=1.0, finished_at=9.0)
        self.assertEqual(ReviewRound.from_dict(stamped.to_dict()).finished_at, 9.0)


class ReviewerModelTests(_Stages):
    """The loop runs every review on the model it was started with."""

    def test_every_review_of_the_loop_runs_on_its_model(self) -> None:
        reviewer = FakeReviewer(reply(finding('MAJOR')), reply())
        state = self.run_loop(FakeChat(), reviewer, extra_sweep=True, model=' sonnet ')
        self.assertEqual(state.status, ReviewLoopStatus.CLEAN, state.reason)
        self.assertEqual(reviewer.models, ['sonnet', 'sonnet', 'sonnet'])  # incl. the sweep
        self.assertEqual(state.summary()['model'], 'sonnet')
        self.assertEqual(ReviewLoopState.from_dict(state.to_dict()).model, 'sonnet')

    def test_no_pick_leaves_the_reviewer_its_own_default(self) -> None:
        reviewer = FakeReviewer(reply())
        state = self.run_loop(FakeChat(), reviewer)
        self.assertEqual(reviewer.models, [''])
        self.assertEqual(state.model, '')
        self.assertEqual(ReviewLoopState.from_dict({
            'task_id': 'T', 'loop_id': 'a' * 32, 'max_rounds': 5, 'started_at': 1.0,
        }).model, '')


class StateAndParsingTests(unittest.TestCase):

    def test_new_fields_round_trip_and_old_states_default(self) -> None:
        state = ReviewLoopState.new('T-1', max_rounds=3, now=1.0, self_check=True,
                                    verify_tests=True, confirm_clean=True)
        state.self_checks = [SelfCheckTurn(1, 1.0, 2.0, clean=None, fixed=0, summary='')]
        state.rounds = [ReviewRound(number=1, started_at=1.0, blind=True, diff_digest='ab12',
                                    tests=TestReport(True, 'pytest', '3 passed', ['x'], 3.0))]
        again = ReviewLoopState.from_dict(json.loads(json.dumps(state.to_dict())))
        self.assertEqual(again.to_dict(), state.to_dict())
        self.assertTrue(again.summary()['confirm_clean'])
        self.assertEqual(again.summary()['self_check_turn'], 1)

        old = ReviewLoopState.from_dict({'task_id': 'T', 'loop_id': 'a' * 32,
                                         'max_rounds': 5, 'started_at': 1.0,
                                         'rounds': [{'number': 1}]})
        self.assertEqual((old.self_check, old.verify_tests, old.confirm_clean), (False,) * 3)
        self.assertEqual(old.self_checks, [])
        self.assertEqual((old.rounds[0].blind, old.rounds[0].tests), (False, None))
        self.assertEqual(old.rounds[0].diff_digest, '')
        self.assertIsNone(TestReport.from_dict({}).passed)
        self.assertEqual(SelfCheckTurn.from_dict({}).clean, None)

    def test_parse_self_check(self) -> None:
        self.assertEqual(parse_self_check(self_check(True, 0, ' ok ')), (True, 0, 'ok'))
        self.assertEqual(parse_self_check(self_check(False, 3)), (False, 3, ''))
        self.assertEqual(parse_self_check('no block'), (None, 0, ''))
        self.assertEqual(parse_self_check(None), (None, 0, ''))
        bad = f'{SELF_CHECK_OPEN}{{"clean": false, "fixed": "many"}}{SELF_CHECK_CLOSE}'
        self.assertEqual(parse_self_check(bad), (False, 0, ''))
        negative = f'{SELF_CHECK_OPEN}{{"fixed": -2}}{SELF_CHECK_CLOSE}'
        self.assertEqual(parse_self_check(negative), (None, 0, ''))
        not_object = f'{SELF_CHECK_OPEN}[1]{SELF_CHECK_CLOSE}'
        self.assertEqual(parse_self_check(not_object), (None, 0, ''))

    def test_parse_test_report(self) -> None:
        report = parse_test_report(test_report(False, failures=[' a ', '', 'b']), now=5.0)
        self.assertEqual((report.passed, report.failures, report.reported_at), (False, ['a', 'b'], 5.0))
        self.assertIsNone(parse_test_report('nothing').passed)
        self.assertIsNone(parse_test_report(None).passed)
        odd = f'{TEST_REPORT_OPEN}{{"passed": true, "failures": "x"}}{TEST_REPORT_CLOSE}'
        self.assertEqual(parse_test_report(odd).failures, [])
        unknown = f'{TEST_REPORT_OPEN}{{"summary": "?"}}{TEST_REPORT_CLOSE}'
        self.assertIsNone(parse_test_report(unknown).passed)

    def test_tests_note(self) -> None:
        self.assertEqual(_tests_note(None), 'the tests were not reported')
        self.assertEqual(_tests_note(TestReport(passed=None)), 'the tests were not reported')
        self.assertEqual(_tests_note(TestReport(True, 'pytest', '')), 'tests passed (pytest)')
        self.assertEqual(_tests_note(TestReport(True)), 'tests passed')

    def test_the_example_blocks_in_the_prompts_parse(self) -> None:
        self_prompt = build_self_check_prompt(turn=1, max_turns=3, wording=WORDING)
        block = self_prompt[self_prompt.index(SELF_CHECK_OPEN):]
        self.assertEqual(parse_self_check(block)[:2], (False, 2))
        tests_prompt = build_tests_prompt(wording=WORDING)
        block = tests_prompt[tests_prompt.index(TEST_REPORT_OPEN):]
        self.assertTrue(parse_test_report(block).passed)

    def test_prompts_without_host_guidance_and_with_no_failures(self) -> None:
        bare = LoopWording(wrap_untrusted=wrap)
        self.assertEqual(build_tests_prompt(wording=bare).splitlines()[0],
                         'Review loop — run the tests')
        self.assertTrue(build_tests_prompt(wording=bare).endswith('to true.\n'))
        fix = build_tests_fix_prompt(task_id='T-1', failures=[], wording=bare)
        self.assertIn('(no details reported)', fix)

    def test_phases_exist_for_the_ui(self) -> None:
        self.assertEqual(ReviewLoopPhase.SELF_CHECK.value, 'self_check')
        self.assertEqual(ReviewLoopPhase.VERIFYING.value, 'verifying')


if __name__ == '__main__':
    unittest.main()
