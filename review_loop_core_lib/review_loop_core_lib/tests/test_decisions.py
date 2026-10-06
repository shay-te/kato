"""The decision ledger: the fixer answers each finding, and later rounds know.

A fixer that rejects a finding WITH evidence has settled it. A fresh reviewer
raising the same issue again without new evidence must not keep the loop
going — before the ledger, that ended the loop as "stuck" on an issue that had
been decided. New evidence reopens it. A bare "I disagree" settles nothing.

Flows run the real service and runner; only the chat and the reviewer are
stand-ins (fakes.py).
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from review_loop_core_lib.review_loop_core_lib.data.state import (
    FindingDecision,
    FindingResponse,
    LedgerEntry,
    ReviewFinding,
    ReviewLoopState,
    ReviewLoopStatus,
    ReviewRound,
    FindingSeverity,
)
from review_loop_core_lib.review_loop_core_lib.diff import render_review_diff
from review_loop_core_lib.review_loop_core_lib.findings_prompt import (
    RESPONSE_CLOSE,
    RESPONSE_OPEN,
    build_findings_prompt,
)
from review_loop_core_lib.review_loop_core_lib.ports import RepoDiff
from review_loop_core_lib.review_loop_core_lib.response import parse_fix_response
from review_loop_core_lib.review_loop_core_lib.reviewer_prompt import build_reviewer_prompt
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
)
from review_loop_core_lib.review_loop_core_lib.verdict import (
    parse_review_verdict,
    settle_findings,
)

DIVIDE = finding('MAJOR', symbol='divide', title='divide multiplies')
AVERAGE = finding('MAJOR', symbol='average', title='average of [] divides by zero')
GO_SOURCE = 'readDirectoryHeader calls extra.sub(fieldSize) first; see archive/zip/reader.go'


def respond(*decisions: dict, prose: str = 'Done.') -> str:
    """A fix turn's reply ending in a response block."""
    return f'{prose}\n{RESPONSE_OPEN}\n{json.dumps({"decisions": list(decisions)})}\n{RESPONSE_CLOSE}'


class _Flow(unittest.TestCase):

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = ReviewLoopStore(Path(tmp.name))
        self.tree = FakeTree()

    def run_loop(self, chat: FakeChat, reviewer: FakeReviewer) -> ReviewLoopState:
        service = ReviewLoopService(
            store=self.store, chat=chat, reviewer=reviewer, wording=WORDING, options=FAST,
        )
        self.addCleanup(service.shutdown, 'test over')
        self.service = service
        service.start('T-1', diff_source=self.tree.diff_source)
        self.assertTrue(wait_until(lambda: not service.is_running('T-1')))
        return service.state('T-1')


class SettledFindingsTests(_Flow):

    def test_a_rejection_with_evidence_settles_the_finding(self) -> None:
        chat = FakeChat(reply=respond(
            {'id': 'R1-1', 'decision': 'fixed', 'test': 'tests/test_calc.py::test_divide',
             'evidence': 'divide divides now'},
            {'id': 'R1-2', 'decision': 'rejected', 'evidence': GO_SOURCE},
        ))
        # Round 2: a fresh reviewer raises the rejected issue again, no new evidence.
        reviewer = FakeReviewer(reply(DIVIDE, AVERAGE), reply(AVERAGE))
        state = self.run_loop(chat, reviewer)

        self.assertEqual(state.status, ReviewLoopStatus.CLEAN, state.reason)
        self.assertEqual(
            state.reason, 'round 2 found no blocking issues (1 raised again were settled earlier)',
        )
        first, second = state.rounds
        self.assertEqual([f.id for f in first.findings], ['R1-1', 'R1-2'])
        self.assertEqual(
            [(r.finding_id, r.decision, r.settles) for r in first.responses],
            [('R1-1', FindingDecision.FIXED, False), ('R1-2', FindingDecision.REJECTED, True)],
        )
        self.assertEqual(first.responses[0].test, 'tests/test_calc.py::test_divide')
        [repeat] = second.findings
        self.assertEqual((repeat.id, repeat.settled_by, repeat.is_blocking), ('R2-1', 'R1-2', False))
        self.assertEqual(second.counts['SETTLED'], 1)
        self.assertEqual(second.counts['MAJOR'], 0)
        self.assertEqual(len(chat.delivered), 1)  # the settled repeat was never sent

        # The second reviewer was told the decisions, framed as untrusted.
        told = reviewer.prompts[1]
        self.assertIn('## Decisions so far', told)
        self.assertIn('<untrusted source="task T-1 review decisions so far">', told)
        self.assertIn('- R1-1 [MAJOR] api/app.py (divide) — divide multiplies: fixed; '
                      'test: tests/test_calc.py::test_divide; evidence: divide divides now', told)
        self.assertIn(f'- R1-2 [MAJOR] api/app.py (average) — average of [] divides by zero: '
                      f'rejected; evidence: {GO_SOURCE}', told)
        self.assertNotIn('## Decisions so far', reviewer.prompts[0])
        # The fix turn's reply is kept for the view.
        reply_text = self.service.artifact('T-1', state.loop_id, 1, ArtifactKind.RESPONSE)
        self.assertIn(GO_SOURCE, reply_text)

    def test_new_evidence_reopens_a_settled_finding(self) -> None:
        answers = iter([
            respond({'id': 'R1-1', 'decision': 'out_of_scope', 'evidence': 'the ticket excludes it'}),
            respond({'id': 'R2-1', 'decision': 'fixed', 'test': 't::x'}),
        ])
        chat = FakeChat(reply=lambda prompt: next(answers))
        reopened = dict(AVERAGE, new_evidence='the ticket says "averages must handle []"')
        reviewer = FakeReviewer(reply(AVERAGE), reply(reopened), reply())
        state = self.run_loop(chat, reviewer)

        self.assertEqual(state.status, ReviewLoopStatus.CLEAN, state.reason)
        self.assertEqual([r.outcome for r in state.rounds], ['sent', 'sent', 'clean'])
        self.assertEqual(state.rounds[1].findings[0].settled_by, '')
        second_message = chat.delivered[1][0]
        self.assertIn('R2-1 [MAJOR]', second_message)
        self.assertIn('Raised again with new evidence: the ticket says "averages must handle []"',
                      second_message)

    def test_a_bare_disagreement_settles_nothing(self) -> None:
        chat = FakeChat(reply=respond({'id': 'R1-1', 'decision': 'rejected', 'evidence': ''}))
        reviewer = FakeReviewer(reply(AVERAGE), reply(AVERAGE))
        state = self.run_loop(chat, reviewer)
        # Still blocking, and nothing changed: stuck, as before the ledger.
        self.assertEqual(state.status, ReviewLoopStatus.STUCK, state.reason)
        self.assertFalse(state.rounds[0].responses[0].settles)

    def test_a_fix_turn_without_a_response_block_is_unanswered_not_a_failure(self) -> None:
        chat = FakeChat(reply='I fixed everything, trust me.')
        reviewer = FakeReviewer(reply(DIVIDE, AVERAGE), reply())
        state = self.run_loop(chat, reviewer)
        self.assertEqual(state.status, ReviewLoopStatus.CLEAN, state.reason)
        self.assertEqual({r.decision for r in state.rounds[0].responses},
                         {FindingDecision.UNANSWERED})

    def test_a_turn_with_no_text_keeps_no_reply_file(self) -> None:
        state = self.run_loop(FakeChat(reply=''), FakeReviewer(reply(DIVIDE), reply()))
        self.assertEqual(state.status, ReviewLoopStatus.CLEAN, state.reason)
        self.assertEqual([r.decision for r in state.rounds[0].responses],
                         [FindingDecision.UNANSWERED])
        self.assertIsNone(self.service.artifact('T-1', state.loop_id, 1, ArtifactKind.RESPONSE))

    def test_a_claimed_fix_that_did_not_land_is_still_stuck(self) -> None:
        chat = FakeChat(reply=respond(
            {'id': 'R1-1', 'decision': 'fixed', 'test': 't::divide'},
            {'id': 'R1-2', 'decision': 'rejected', 'evidence': GO_SOURCE},
        ))
        # Round 2: divide is still broken (claimed fixed); average is settled.
        reviewer = FakeReviewer(reply(DIVIDE, AVERAGE), reply(DIVIDE, AVERAGE))
        state = self.run_loop(chat, reviewer)
        self.assertEqual(state.status, ReviewLoopStatus.STUCK, state.reason)
        self.assertEqual([f.settled_by for f in state.rounds[1].findings], ['', 'R1-2'])


class ParseFixResponseTests(unittest.TestCase):

    def sent(self, count: int = 3) -> list[ReviewFinding]:
        return [
            ReviewFinding(FindingSeverity.MAJOR, f't{i}', symbol=f's{i}', id=f'R2-{i}')
            for i in range(1, count + 1)
        ]

    def test_every_sent_finding_gets_an_answer_in_order(self) -> None:
        text = respond(
            {'id': 'r2-3', 'decision': 'Out of scope', 'evidence': ' later ticket '},
            {'id': 'R2-1', 'decision': 'FIXED', 'test': ' t::a ', 'evidence': None},
            {'id': 'R9-9', 'decision': 'fixed'},  # not sent: ignored
            'not an object',
        )
        responses = parse_fix_response(text, self.sent())
        self.assertEqual(
            [(r.finding_id, r.decision, r.evidence, r.test) for r in responses],
            [('R2-1', FindingDecision.FIXED, '', 't::a'),
             ('R2-2', FindingDecision.UNANSWERED, '', ''),
             ('R2-3', FindingDecision.OUT_OF_SCOPE, 'later ticket', '')],
        )
        self.assertEqual(responses[0].fingerprint, self.sent()[0].fingerprint)

    def test_the_last_block_wins_and_synonyms_are_understood(self) -> None:
        quoted = respond({'id': 'R2-1', 'decision': 'fixed'}, prose='The format is:')
        text = quoted + '\n' + respond(
            {'id': 'R2-1', 'decision': 'not-applicable', 'evidence': 'e'},
            {'id': 'R2-2', 'decision': 'deferred', 'evidence': 'e'},
            {'id': 'R2-3', 'decision': 'maybe later'},
        )
        decisions = [r.decision for r in parse_fix_response(text, self.sent())]
        self.assertEqual(decisions, [FindingDecision.REJECTED, FindingDecision.OUT_OF_SCOPE,
                                     FindingDecision.UNANSWERED])

    def test_an_unusable_reply_leaves_everything_unanswered(self) -> None:
        for text in ('', None, 'no block at all', f'{RESPONSE_OPEN} not json {RESPONSE_CLOSE}',
                     f'{RESPONSE_OPEN} [1, 2] {RESPONSE_CLOSE}',
                     f'{RESPONSE_OPEN} {{"decisions": "all fixed"}} {RESPONSE_CLOSE}',
                     f'{RESPONSE_OPEN} {{"decisions": []}}', f'{RESPONSE_CLOSE} {RESPONSE_OPEN}'):
            with self.subTest(text=text):
                responses = parse_fix_response(text, self.sent(2))
                self.assertEqual([r.decision for r in responses], [FindingDecision.UNANSWERED] * 2)


class VerdictIdsAndSettlingTests(unittest.TestCase):

    def test_ids_follow_the_reviewers_order_and_new_fields_are_read(self) -> None:
        text = reply(dict(DIVIDE, invariant=' divide(a, b) == a / b '),
                     dict(AVERAGE, new_evidence=' see the ticket '))
        verdict = parse_review_verdict(text, round_number=3)
        self.assertEqual([f.id for f in verdict.findings], ['R3-1', 'R3-2'])
        self.assertEqual(verdict.findings[0].invariant, 'divide(a, b) == a / b')
        self.assertEqual(verdict.findings[1].new_evidence, 'see the ticket')
        self.assertEqual([f.id for f in parse_review_verdict(text).findings], ['', ''])

    def ledger(self, decision: FindingDecision, evidence: str) -> list[LedgerEntry]:
        earlier = parse_review_verdict(reply(AVERAGE), round_number=1).findings[0]
        return [LedgerEntry(earlier, FindingResponse(
            earlier.id, earlier.fingerprint, decision, evidence=evidence))]

    def test_only_a_blocking_repeat_without_new_evidence_is_settled(self) -> None:
        verdict = parse_review_verdict(reply(
            AVERAGE,
            dict(AVERAGE, new_evidence='new'),
            dict(AVERAGE, severity='MINOR'),
            DIVIDE,
        ), round_number=2)
        settled = settle_findings(verdict, self.ledger(FindingDecision.REJECTED, 'proof'))
        self.assertEqual([f.settled_by for f in settled.findings], ['R1-1', '', '', ''])
        self.assertEqual([f.id for f in settled.blocking], ['R2-2', 'R2-4'])
        self.assertEqual([f.id for f in settled.settled], ['R2-1'])

    def test_nothing_settled_leaves_the_verdict_as_it_was(self) -> None:
        verdict = parse_review_verdict(reply(AVERAGE), round_number=2)
        for decision, evidence in ((FindingDecision.REJECTED, ''), (FindingDecision.FIXED, 'x'),
                                   (FindingDecision.UNANSWERED, 'x')):
            with self.subTest(decision=decision):
                self.assertIs(settle_findings(verdict, self.ledger(decision, evidence)), verdict)
        self.assertIs(settle_findings(verdict, []), verdict)


class LedgerStateTests(unittest.TestCase):

    def test_the_latest_decision_per_issue_wins_oldest_issue_first(self) -> None:
        def round_with(number, findings, responses):
            verdict = parse_review_verdict(reply(*findings), round_number=number)
            sent = list(verdict.findings)
            return ReviewRound(number=number, started_at=0.0, findings=sent, responses=[
                FindingResponse(sent[i].id, sent[i].fingerprint, decision, evidence=evidence)
                for i, decision, evidence in responses
            ])

        state = ReviewLoopState.new('T-1', max_rounds=5, now=0.0)
        state.rounds = [
            round_with(1, [DIVIDE, AVERAGE], [(0, FindingDecision.FIXED, ''),
                                              (1, FindingDecision.REJECTED, 'proof')]),
            round_with(2, [dict(AVERAGE, new_evidence='n')], [(0, FindingDecision.FIXED, '')]),
        ]
        # A response naming a finding the round does not have is ignored.
        state.rounds[1].responses.append(FindingResponse('R2-9', 'nope', FindingDecision.FIXED))
        ledger = state.ledger()
        self.assertEqual([(e.finding.id, e.response.decision) for e in ledger],
                         [('R1-1', FindingDecision.FIXED), ('R2-1', FindingDecision.FIXED)])
        self.assertEqual(state.rounds[0].claimed_fixed_fingerprints,
                         {state.rounds[0].findings[0].fingerprint})

        again = ReviewLoopState.from_dict(json.loads(json.dumps(state.to_dict())))
        self.assertEqual(again.rounds[0].responses, state.rounds[0].responses)
        self.assertEqual(again.rounds[1].findings, state.rounds[1].findings)
        self.assertTrue(again.to_dict()['rounds'][0]['responses'][1]['settles'])

    def test_older_saved_rounds_without_a_ledger_read_back(self) -> None:
        old = ReviewRound.from_dict({'number': 1, 'findings': [{'severity': 'MAJOR'}]})
        self.assertEqual(old.responses, [])
        self.assertEqual((old.findings[0].id, old.findings[0].settled_by), ('', ''))
        self.assertEqual(FindingResponse.from_dict({}).decision, FindingDecision.UNANSWERED)


class PromptTests(unittest.TestCase):

    def test_the_findings_message_names_ids_invariants_and_the_response_contract(self) -> None:
        verdict = parse_review_verdict(
            reply(dict(DIVIDE, invariant='divide(a, b) == a / b')), round_number=1,
        )
        prompt = build_findings_prompt(task_id='T-1', verdict=verdict, round_number=1,
                                       max_rounds=5, wording=WORDING)
        self.assertIn('1. R1-1 [MAJOR] api/app.py:3 (divide) — divide multiplies\n'
                      '   fix it\n   Invariant: divide(a, b) == a / b', prompt)
        self.assertIn('fix the ROOT CAUSE', prompt)
        self.assertIn('regression test that fails without the fix', prompt)
        self.assertIn(f'{RESPONSE_OPEN}\n{{"decisions": [{{"id": "R1-1"', prompt)
        # The example in the prompt is itself a valid answer.
        example = prompt[prompt.index(RESPONSE_OPEN):prompt.index(RESPONSE_CLOSE) + len(RESPONSE_CLOSE)]
        [answer] = parse_fix_response(example, verdict.findings)
        self.assertIs(answer.decision, FindingDecision.FIXED)

    def test_the_reviewer_is_asked_for_an_invariant_and_told_about_new_evidence(self) -> None:
        diffs = [RepoDiff(repo_id='api', diff='diff --git a/x b/x\n+1\n')]
        prompt = build_reviewer_prompt(
            task_id='T-1', task_summary='', task_description='', diffs=diffs,
            rendered=render_review_diff(diffs, budget_chars=1000), wording=WORDING,
        )
        self.assertIn('"invariant": "the rule the code must keep", "new_evidence": ""', prompt)
        self.assertIn('`new_evidence` is only for raising again an issue', prompt)


if __name__ == '__main__':
    unittest.main()
