"""A-Z flows of a review loop, through the public service.

Each flow starts a loop the way a host does and watches it end the way an
operator would: the status, every round's numbers, what reached the chat, and
what is on disk for the view.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from review_loop_core_lib.review_loop_core_lib.data.state import (
    ReviewLoopPhase,
    ReviewLoopState,
    ReviewLoopStatus,
)
from review_loop_core_lib.review_loop_core_lib.ports import ChatReadiness
from review_loop_core_lib.review_loop_core_lib.service import ReviewLoopService
from review_loop_core_lib.review_loop_core_lib.store import (
    ArtifactKind,
    ReviewLoopStore,
)
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


class _Flow(unittest.TestCase):

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.store = ReviewLoopStore(self.root)
        self.tree = FakeTree()
        self.events: list[str] = []

    def service(self, chat: FakeChat, reviewer: FakeReviewer, **kwargs) -> ReviewLoopService:
        service = ReviewLoopService(
            store=self.store, chat=chat, reviewer=reviewer, wording=WORDING,
            observer=lambda state, event: self.events.append(event),
            options=FAST, **kwargs,
        )
        self.addCleanup(service.shutdown, 'test over')
        return service

    def run_to_end(
        self, service: ReviewLoopService, task_id: str = 'T-1', **options,
    ) -> ReviewLoopState:
        service.start(
            task_id, diff_source=self.tree.diff_source,
            task_summary='Add login', task_description='Users log in with email.', **options,
        )
        self.assertTrue(wait_until(lambda: not service.is_running(task_id)))
        return service.state(task_id)


class CleanAfterAFixTests(_Flow):

    def test_one_fix_round_then_clean(self) -> None:
        def fixer(prompt: str) -> None:
            self.tree.files['app.py'] = 'print(2)  # FIXED_BY_THE_AGENT\n'

        chat = FakeChat(fixer=fixer)
        reviewer = FakeReviewer(reply(finding('MAJOR'), finding('NIT', symbol='x')), reply())
        state = self.run_to_end(self.service(chat, reviewer))

        self.assertEqual(state.status, ReviewLoopStatus.CLEAN)
        self.assertEqual(state.phase, ReviewLoopPhase.DONE)
        self.assertEqual([r.outcome for r in state.rounds], ['sent', 'clean'])
        first, second = state.rounds
        self.assertEqual(first.counts, {'BLOCKER': 0, 'MAJOR': 1, 'MINOR': 0, 'NIT': 1, 'SETTLED': 0})
        self.assertTrue(first.reviewed_at and first.sent_at and first.fixed_at)
        self.assertEqual(second.findings, [])

        # One message reached the chat: the round-1 findings, header first,
        # the MAJOR in it, the NIT left out.
        self.assertEqual(len(chat.delivered), 1)
        prompt, forced = chat.delivered[0]
        self.assertFalse(forced)
        self.assertTrue(prompt.startswith('Host review loop — round 1 of 5\n'))
        self.assertIn('[MAJOR]', prompt)
        self.assertNotIn('[NIT]', prompt)
        self.assertIn('Never print the done marker.', prompt)

        # The second review saw the fixed tree: each round re-reads the diff.
        self.assertIn('FIXED_BY_THE_AGENT', reviewer.prompts[1])
        self.assertNotIn('FIXED_BY_THE_AGENT', reviewer.prompts[0])
        self.assertIn('Users log in with email.', reviewer.prompts[0])

        self.assertEqual(
            self.events,
            ['started', 'reviewing', 'reviewed', 'sent', 'fixed',
             'reviewing', 'reviewed', 'finished'],
        )

    def test_every_round_is_on_disk_for_the_view(self) -> None:
        chat = FakeChat()
        reviewer = FakeReviewer(reply(finding('BLOCKER')), reply())
        service = self.service(chat, reviewer)
        state = self.run_to_end(service)
        # What a plain fix round leaves (the self-check / tests kinds only
        # exist when those options are on — see test_stages.py).
        for kind in (ArtifactKind.DIFF, ArtifactKind.REVIEW, ArtifactKind.PROMPT,
                     ArtifactKind.RESPONSE):
            self.assertTrue(service.artifact('T-1', state.loop_id, 1, kind))
        self.assertIn('diff --git a/app.py', service.artifact('T-1', state.loop_id, 1, 'diff'))
        self.assertIsNone(service.artifact('T-1', state.loop_id, 2, ArtifactKind.PROMPT))
        # A fresh service (a restarted host) reads the same finished loop back.
        reread = ReviewLoopService(
            store=ReviewLoopStore(self.root), chat=chat, reviewer=reviewer, wording=WORDING,
        )
        self.assertEqual(reread.state('T-1').status, ReviewLoopStatus.CLEAN)
        self.assertEqual(reread.summaries()['T-1']['round'], 2)

    def test_nits_alone_are_clean_on_the_first_round(self) -> None:
        chat = FakeChat()
        state = self.run_to_end(self.service(chat, FakeReviewer(reply(finding('MINOR'), finding('NIT')))))
        self.assertEqual(state.status, ReviewLoopStatus.CLEAN)
        self.assertEqual(chat.delivered, [])


class LoopsThatDoNotConvergeTests(_Flow):

    def test_an_issue_that_survives_two_fixes_is_stuck(self) -> None:
        chat = FakeChat()
        reviewer = FakeReviewer(reply(finding('MAJOR', title='first wording')),
                                reply(finding('MAJOR', title='said differently')))
        state = self.run_to_end(self.service(chat, reviewer))
        self.assertEqual(state.status, ReviewLoopStatus.STUCK)
        # Sent, sent again as a repeat, then stuck: one miss is not enough.
        self.assertEqual([r.outcome for r in state.rounds], ['sent', 'sent', 'stuck'])
        self.assertEqual(len(chat.delivered), 2)
        self.assertEqual(state.reason, 'R1-1 still found after 2 fixes')
        repeat = state.rounds[1].findings[0]
        self.assertEqual((repeat.repeat_of, repeat.missed_fixes), ('R1-1', 1))
        second_message = chat.delivered[1][0]
        self.assertIn('1 of them you said were fixed, and the review still finds them', second_message)
        self.assertIn('Still here: first reported as R1-1, and it survived 1 fix(es)', second_message)

    def test_a_fix_that_lands_on_the_second_try_ends_clean(self) -> None:
        chat = FakeChat()
        reviewer = FakeReviewer(reply(finding('MAJOR')), reply(finding('MAJOR')), reply())
        state = self.run_to_end(self.service(chat, reviewer))
        self.assertEqual(state.status, ReviewLoopStatus.CLEAN, state.reason)
        self.assertEqual([r.outcome for r in state.rounds], ['sent', 'sent', 'clean'])

    def test_new_blocking_issues_are_always_sent_never_stuck_on(self) -> None:
        # A keeps coming back; B joins in round 2. Round 3: A has missed twice
        # but B only once — still work to send. Round 4: both missed twice.
        chat = FakeChat()
        a, b = finding('MAJOR', symbol='run'), finding('MAJOR', symbol='load')
        reviewer = FakeReviewer(reply(a), reply(a, b))
        state = self.run_to_end(self.service(chat, reviewer))
        self.assertEqual([r.outcome for r in state.rounds], ['sent', 'sent', 'sent', 'stuck'])
        self.assertEqual(state.reason, 'R1-1, R2-2 still found after 2 fixes')

    def test_the_screenshot_case_goes_on_with_the_new_issues(self) -> None:
        # Clean, a clean-room sweep finds one, the fix is reviewed and three
        # are found (the old one + two new): the old rule stopped here.
        chat = FakeChat()
        a, b, c = (finding('MAJOR', symbol=name) for name in ('run', 'load', 'save'))
        reviewer = FakeReviewer(reply(), reply(a), reply(a, b, c), reply(), reply())
        state = self.run_to_end(self.service(chat, reviewer), extra_sweep=True)
        self.assertEqual(state.status, ReviewLoopStatus.CLEAN, state.reason)
        self.assertEqual([r.outcome for r in state.rounds],
                         ['clean', 'sent', 'sent', 'clean', 'clean'])
        self.assertEqual([f.repeat_of for f in state.rounds[2].blocking], ['R2-1', '', ''])

    def test_five_reviews_at_most_and_the_last_is_not_sent(self) -> None:
        chat = FakeChat()
        replies = [reply(finding('MAJOR', symbol=f'fn{n}')) for n in range(1, 6)]
        reviewer = FakeReviewer(*replies)
        state = self.run_to_end(self.service(chat, reviewer))
        self.assertEqual(state.status, ReviewLoopStatus.MAX_ROUNDS)
        self.assertEqual(len(state.rounds), 5)
        self.assertEqual(len(chat.delivered), 4)
        self.assertEqual(
            [prompt.splitlines()[0] for prompt, _ in chat.delivered],
            [f'Host review loop — round {n} of 5' for n in range(1, 5)],
        )
        self.assertEqual(state.rounds[-1].outcome, 'max_rounds')
        self.assertIn('1 blocking issue(s) left after 5 reviews', state.reason)

    def test_a_smaller_cap_is_honoured(self) -> None:
        chat = FakeChat()
        reviewer = FakeReviewer(reply(finding('MAJOR', symbol='a')), reply(finding('MAJOR', symbol='b')))
        state = self.run_to_end(self.service(chat, reviewer, max_rounds=2))
        self.assertEqual(state.status, ReviewLoopStatus.MAX_ROUNDS)
        self.assertEqual(len(chat.delivered), 1)


class TheChatComesFirstTests(_Flow):

    def test_the_loop_waits_for_a_busy_chat_and_says_why(self) -> None:
        chat = FakeChat()
        chat.default_readiness = ChatReadiness.wait('the agent is mid-turn')
        reviewer = FakeReviewer(reply())
        service = self.service(chat, reviewer)
        service.start('T-1', diff_source=self.tree.diff_source)
        self.assertTrue(wait_until(
            lambda: service.state('T-1').waiting_for == 'the agent is mid-turn',
        ))
        state = service.state('T-1')
        self.assertEqual(state.phase, ReviewLoopPhase.WAITING_TO_REVIEW)
        self.assertEqual(state.round, 1)
        self.assertEqual(reviewer.prompts, [])  # nothing reviewed while busy
        chat.default_readiness = ChatReadiness.ready()
        self.assertTrue(wait_until(lambda: not service.is_running('T-1')))
        self.assertEqual(service.state('T-1').status, ReviewLoopStatus.CLEAN)

    def test_a_read_only_chat_stops_the_loop(self) -> None:
        chat = FakeChat()
        chat.default_readiness = ChatReadiness.refuse('the chat is in plan mode')
        state = self.run_to_end(self.service(chat, FakeReviewer(reply())))
        self.assertEqual(state.status, ReviewLoopStatus.STOPPED)
        self.assertEqual(state.reason, 'the chat is in plan mode')


class RestartAndRemovalTests(_Flow):

    def test_a_loop_left_running_by_a_dead_process_is_interrupted(self) -> None:
        orphan = ReviewLoopState.new('T-9', max_rounds=5, now=1.0)
        self.store.save(orphan)
        service = self.service(FakeChat(), FakeReviewer(reply()))
        self.assertEqual(service.mark_interrupted('the host restarted'), 1)
        state = service.state('T-9')
        self.assertEqual(state.status, ReviewLoopStatus.INTERRUPTED)
        self.assertEqual(state.reason, 'the host restarted')
        self.assertEqual(ReviewLoopStore(self.root).latest('T-9').status, ReviewLoopStatus.INTERRUPTED)
        self.assertEqual(service.mark_interrupted('again'), 0)
        # And a new loop may start for the task.
        self.tree = FakeTree()
        self.assertEqual(self.run_to_end(service, 'T-9').status, ReviewLoopStatus.CLEAN)

    def test_removing_a_task_mid_review_leaves_nothing_behind(self) -> None:
        reviewer = FakeReviewer(block_until_cancelled=True)
        service = self.service(FakeChat(), reviewer)
        service.start('T-1', diff_source=self.tree.diff_source)
        self.assertTrue(reviewer.started.wait(5))
        service.forget('T-1')
        self.assertFalse(service.is_running('T-1'))
        self.assertFalse((self.root / 'T-1').exists())
        self.assertIsNone(service.state('T-1'))
        self.assertNotIn('T-1', service.summaries())


if __name__ == '__main__':
    unittest.main()
