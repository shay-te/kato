"""Every way a running loop can end other than clean, and how it waits.

Stops arrive mid-review and mid-fix; the chat goes busy, read-only, stalled,
or away; the reviewer fails or answers nonsense. Each ends with a status and a
reason an operator can act on — never a hang and never a silent "clean".
"""
from __future__ import annotations

import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path

from review_loop_core_lib.review_loop_core_lib.data.state import (
    ReviewLoopPhase,
    ReviewLoopState,
    ReviewLoopStatus,
)
from review_loop_core_lib.review_loop_core_lib.ports import ChatReadiness, RepoDiff
from review_loop_core_lib.review_loop_core_lib.runner import ReviewLoopRunner
from review_loop_core_lib.review_loop_core_lib.service import ReviewLoopService
from review_loop_core_lib.review_loop_core_lib.store import ReviewLoopStore
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


class _Runs(unittest.TestCase):

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = ReviewLoopStore(Path(tmp.name))
        self.tree = FakeTree()

    def service(self, chat, reviewer, *, options=FAST, **kwargs) -> ReviewLoopService:
        service = ReviewLoopService(
            store=self.store, chat=chat, reviewer=reviewer, wording=WORDING,
            options=options, **kwargs,
        )
        self.addCleanup(service.shutdown, 'test over')
        return service

    def start(self, service, diff_source=None) -> None:
        service.start('T-1', diff_source=diff_source or self.tree.diff_source)

    def finished(self, service) -> ReviewLoopState:
        self.assertTrue(wait_until(lambda: not service.is_running('T-1')))
        return service.state('T-1')


class StopTests(_Runs):

    def test_stop_during_the_review_kills_it_and_sends_nothing(self) -> None:
        chat = FakeChat()
        reviewer = FakeReviewer(block_until_cancelled=True)
        service = self.service(chat, reviewer)
        self.start(service)
        self.assertTrue(reviewer.started.wait(5))
        self.assertTrue(service.stop('T-1'))
        state = self.finished(service)
        self.assertEqual(state.status, ReviewLoopStatus.STOPPED)
        self.assertEqual(state.reason, 'stopped by the operator')
        self.assertEqual(state.rounds[-1].outcome, 'stopped')
        self.assertEqual(chat.delivered, [])
        self.assertFalse(service.stop('T-1'))  # nothing running any more

    def test_stop_while_the_chat_is_fixing(self) -> None:
        chat = FakeChat()
        chat.answer_turns = False  # the fix turn never ends on its own
        service = self.service(chat, FakeReviewer(reply(finding('MAJOR'))))
        self.start(service)
        self.assertTrue(wait_until(lambda: service.state('T-1').phase is ReviewLoopPhase.AWAITING_FIX))
        service.stop('T-1', reason='main chat stopped')
        state = self.finished(service)
        self.assertEqual(state.status, ReviewLoopStatus.STOPPED)
        self.assertEqual(state.reason, 'main chat stopped')
        self.assertEqual(state.rounds[-1].outcome, 'sent')
        self.assertEqual(len(chat.delivered), 1)

    def test_shutdown_marks_running_loops_interrupted(self) -> None:
        reviewer = FakeReviewer(block_until_cancelled=True)
        service = self.service(FakeChat(), reviewer)
        self.start(service)
        self.assertTrue(reviewer.started.wait(5))
        service.shutdown('host shutting down')
        state = self.finished(service)
        self.assertEqual(state.status, ReviewLoopStatus.INTERRUPTED)
        self.assertEqual(state.reason, 'host shutting down')


class TheChatGoesWrongTests(_Runs):

    def test_a_fix_turn_that_errors_fails_the_loop(self) -> None:
        chat = FakeChat(turn_error=True)
        service = self.service(chat, FakeReviewer(reply(finding('MAJOR'))))
        self.start(service)
        state = self.finished(service)
        self.assertEqual(state.status, ReviewLoopStatus.FAILED)
        self.assertIn('ended with an error', state.reason)

    def test_a_chat_whose_session_disappears_fails_after_a_grace_period(self) -> None:
        chat = FakeChat()
        chat.answer_turns = False
        service = self.service(chat, FakeReviewer(reply(finding('MAJOR'))))
        self.start(service)
        self.assertTrue(wait_until(lambda: service.state('T-1').phase is ReviewLoopPhase.AWAITING_FIX))
        chat.alive = False
        state = self.finished(service)
        self.assertEqual(state.status, ReviewLoopStatus.FAILED)
        self.assertIn('session ended', state.reason)

    def test_a_session_that_comes_back_within_the_grace_is_fine(self) -> None:
        chat = FakeChat()
        chat.answer_turns = False
        options = replace(FAST, session_gone_grace_seconds=5.0)
        service = self.service(chat, FakeReviewer(reply(finding('MAJOR')), reply()), options=options)
        self.start(service)
        self.assertTrue(wait_until(lambda: service.state('T-1').phase is ReviewLoopPhase.AWAITING_FIX))
        chat.alive = False
        time.sleep(0.05)
        chat.alive = True
        chat.answer_turns = True
        chat.deliver('T-1', 'the operator nudged it', force_respawn=False)
        self.assertEqual(self.finished(service).status, ReviewLoopStatus.CLEAN)

    def test_a_fix_waiting_on_an_approval_says_so_and_is_never_respawned(self) -> None:
        chat = FakeChat()
        chat.answer_turns = False
        service = self.service(chat, FakeReviewer(reply(finding('MAJOR')), reply()))
        self.start(service)
        self.assertTrue(wait_until(lambda: service.state('T-1').phase is ReviewLoopPhase.AWAITING_FIX))
        # The turn is paused on a person; even reading as stalled must not
        # get it killed and respawned.
        chat.awaiting_approval = 'Write'
        chat.stalled = True
        self.assertTrue(wait_until(
            lambda: service.state('T-1').waiting_for == 'waiting for your approval in the chat (Write)',
        ))
        time.sleep(0.05)
        self.assertEqual([forced for _, forced in chat.delivered], [False])
        # Approved: the turn finishes and the loop moves on.
        chat.awaiting_approval = ''
        chat.stalled = False
        chat.answer_turns = True
        chat.deliver('T-1', 'the operator approved; the agent finished', force_respawn=False)
        self.assertEqual(self.finished(service).status, ReviewLoopStatus.CLEAN)

    def test_a_stalled_chat_is_respawned_once_then_given_up_on(self) -> None:
        chat = FakeChat()
        chat.answer_turns = False
        service = self.service(chat, FakeReviewer(reply(finding('MAJOR'))))
        self.start(service)
        self.assertTrue(wait_until(lambda: service.state('T-1').phase is ReviewLoopPhase.AWAITING_FIX))
        chat.stalled = True
        state = self.finished(service)
        self.assertEqual(state.status, ReviewLoopStatus.FAILED)
        self.assertIn('stopped reading its input twice', state.reason)
        self.assertEqual([forced for _, forced in chat.delivered], [False, True])

    def test_a_stalled_chat_at_send_time_is_respawned_with_the_findings(self) -> None:
        chat = FakeChat()
        chat.stalled = True
        service = self.service(chat, FakeReviewer(reply(finding('MAJOR')), reply()))
        self.start(service)
        self.assertEqual(self.finished(service).status, ReviewLoopStatus.CLEAN)
        self.assertEqual(chat.delivered[0][1], True)

    def test_a_chat_that_refuses_after_the_fix_stops_the_loop(self) -> None:
        chat = FakeChat()
        service = self.service(chat, FakeReviewer(reply(finding('MAJOR'))))
        original = chat.deliver

        def deliver_then_lock(task_id, prompt, *, force_respawn):
            delivered = original(task_id, prompt, force_respawn=force_respawn)
            chat.default_readiness = ChatReadiness.refuse('switched to plan mode')
            return delivered

        chat.deliver = deliver_then_lock
        self.start(service)
        state = self.finished(service)
        self.assertEqual(state.status, ReviewLoopStatus.STOPPED)
        self.assertEqual(state.reason, 'switched to plan mode')

    def test_a_turn_followed_by_more_work_waits_for_all_of_it(self) -> None:
        chat = FakeChat()
        service = self.service(chat, FakeReviewer(reply(finding('MAJOR')), reply()))
        original = chat.deliver

        def deliver_then_busy(task_id, prompt, *, force_respawn):
            delivered = original(task_id, prompt, force_respawn=force_respawn)
            chat.readiness_script = [ChatReadiness.wait('a background task is running')] * 5
            return delivered

        chat.deliver = deliver_then_busy
        seen: list[str] = []
        service._observer = lambda state, event: seen.append(state.waiting_for)
        self.start(service)
        self.assertEqual(self.finished(service).status, ReviewLoopStatus.CLEAN)

    def test_a_chat_that_never_frees_up_fails_the_loop(self) -> None:
        chat = FakeChat()
        chat.default_readiness = ChatReadiness.wait('busy forever')
        options = replace(FAST, chat_wait_timeout_seconds=0.05)
        service = self.service(chat, FakeReviewer(reply()), options=options)
        self.start(service)
        state = self.finished(service)
        self.assertEqual(state.status, ReviewLoopStatus.FAILED)
        self.assertIn('was not free', state.reason)

    def test_a_fix_that_never_ends_fails_the_loop(self) -> None:
        chat = FakeChat()
        chat.answer_turns = False
        options = replace(FAST, fix_turn_timeout_seconds=0.05)
        service = self.service(chat, FakeReviewer(reply(finding('MAJOR'))), options=options)
        self.start(service)
        state = self.finished(service)
        self.assertEqual(state.status, ReviewLoopStatus.FAILED)
        self.assertIn('fix took longer', state.reason)

    def test_an_undeliverable_message_fails_the_loop(self) -> None:
        chat = FakeChat()
        chat.deliver = lambda task_id, prompt, *, force_respawn: False
        service = self.service(chat, FakeReviewer(reply(finding('MAJOR'))))
        self.start(service)
        state = self.finished(service)
        self.assertEqual(state.status, ReviewLoopStatus.FAILED)
        self.assertIn('could not be delivered', state.reason)

    def test_settling_is_shown_while_the_chat_has_just_freed_up(self) -> None:
        chat = FakeChat()
        options = replace(FAST, settle_seconds=0.3)
        service = self.service(chat, FakeReviewer(reply()), options=options)
        self.start(service)
        self.assertTrue(wait_until(lambda: service.state('T-1').waiting_for == 'letting the chat settle'))
        self.assertEqual(self.finished(service).status, ReviewLoopStatus.CLEAN)


class OneSenderAtATimeTests(_Runs):

    def test_the_send_waits_for_whoever_holds_the_chat_lock(self) -> None:
        chat = FakeChat()
        service = self.service(chat, FakeReviewer(reply(finding('MAJOR')), reply()))
        chat.lock.acquire()  # another sender (a comment run) is mid-send
        self.start(service)
        self.assertTrue(wait_until(lambda: service.state('T-1').phase is ReviewLoopPhase.WAITING_TO_SEND))
        time.sleep(0.05)
        self.assertEqual(chat.delivered, [])
        chat.lock.release()
        self.assertEqual(self.finished(service).status, ReviewLoopStatus.CLEAN)
        self.assertEqual(len(chat.delivered), 1)

    def test_a_chat_taken_meanwhile_is_re_checked_under_the_lock(self) -> None:
        chat = FakeChat()
        service = self.service(chat, FakeReviewer(reply(finding('MAJOR')), reply()))
        # Free for the wait, busy at the re-check, then free again.
        chat.readiness_script = [ChatReadiness.ready()] * 2 + [ChatReadiness.wait('a comment run took it')]
        self.start(service)
        self.assertEqual(self.finished(service).status, ReviewLoopStatus.CLEAN)
        self.assertEqual(len(chat.delivered), 1)

    def test_a_refusal_at_the_re_check_stops_the_loop(self) -> None:
        chat = FakeChat()
        service = self.service(chat, FakeReviewer(reply(finding('MAJOR'))))
        chat.readiness_script = [ChatReadiness.ready()] * 2 + [ChatReadiness.refuse('plan mode')]
        self.start(service)
        state = self.finished(service)
        self.assertEqual(state.status, ReviewLoopStatus.STOPPED)
        self.assertEqual(chat.delivered, [])


class TheReviewGoesWrongTests(_Runs):

    def test_no_changes_is_a_failure_with_a_reason(self) -> None:
        empty = lambda task_id: [RepoDiff(repo_id='api', diff='')]  # noqa: E731
        reviewer = FakeReviewer(reply())
        service = self.service(FakeChat(), reviewer)
        self.start(service, empty)
        state = self.finished(service)
        self.assertEqual(state.status, ReviewLoopStatus.FAILED)
        self.assertEqual(state.reason, 'there are no changes to review')
        self.assertEqual(reviewer.prompts, [])

    def test_a_reply_without_a_verdict_fails_instead_of_guessing(self) -> None:
        service = self.service(FakeChat(), FakeReviewer('Looks fine to me!'))
        self.start(service)
        state = self.finished(service)
        self.assertEqual(state.status, ReviewLoopStatus.FAILED)
        self.assertIn('no usable verdict', state.reason)

    def test_a_failing_review_run_fails_the_loop(self) -> None:
        service = self.service(FakeChat(), FakeReviewer())
        self.start(service)
        state = self.finished(service)
        self.assertEqual(state.status, ReviewLoopStatus.FAILED)
        self.assertIn('the review run failed: no scripted reply left', state.reason)

    def test_an_unexpected_error_still_ends_the_loop(self) -> None:
        def broken(task_id):
            raise KeyError('diff source blew up')

        service = self.service(FakeChat(), FakeReviewer(reply()))
        self.start(service, broken)
        state = self.finished(service)
        self.assertEqual(state.status, ReviewLoopStatus.FAILED)
        self.assertIn('unexpected error', state.reason)

    def test_a_broken_observer_never_breaks_the_loop(self) -> None:
        def observer(state, event):
            raise RuntimeError('notification channel down')

        service = self.service(FakeChat(), FakeReviewer(reply()), observer=observer)
        self.start(service)
        self.assertEqual(self.finished(service).status, ReviewLoopStatus.CLEAN)


class RunnerControlTests(unittest.TestCase):

    def test_cancel_after_the_end_and_join_without_a_thread_are_harmless(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = ReviewLoopState.new('T-1', max_rounds=1, now=1.0)
            runner = ReviewLoopRunner(
                state, diff_source=FakeTree().diff_source, reviewer=FakeReviewer(reply()),
                chat=FakeChat(), store=ReviewLoopStore(tmp), wording=WORDING, options=FAST,
            )
            runner.join(0.01)
            self.assertFalse(runner.is_alive)
            runner.start()
            runner.join(5)
            self.assertEqual(runner.snapshot().status, ReviewLoopStatus.CLEAN)
            runner.cancel('too late')
            self.assertEqual(runner.snapshot().status, ReviewLoopStatus.CLEAN)
            runner._finish(ReviewLoopStatus.FAILED, 'ignored once final')
            self.assertEqual(runner.snapshot().status, ReviewLoopStatus.CLEAN)

    def test_a_cancel_during_a_sleep_ends_the_wait(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            chat = FakeChat()
            chat.default_readiness = ChatReadiness.wait('busy')
            state = ReviewLoopState.new('T-1', max_rounds=1, now=1.0)
            runner = ReviewLoopRunner(
                state, diff_source=FakeTree().diff_source, reviewer=FakeReviewer(reply()),
                chat=chat, store=ReviewLoopStore(tmp), wording=WORDING,
                options=replace(FAST, poll_seconds=30.0),
            )
            runner.start()
            time.sleep(0.05)
            started = time.monotonic()
            runner.cancel('stop now')
            runner.join(5)
            self.assertLess(time.monotonic() - started, 5)
            self.assertEqual(runner.snapshot().status, ReviewLoopStatus.STOPPED)

    def test_the_thread_is_named_for_the_task(self) -> None:
        names: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            reviewer = FakeReviewer(reply())
            original = reviewer.review

            def recording(prompt, *, task_id, cancel_event):
                names.append(threading.current_thread().name)
                return original(prompt, task_id=task_id, cancel_event=cancel_event)

            reviewer.review = recording
            runner = ReviewLoopRunner(
                ReviewLoopState.new('T-7', max_rounds=1, now=1.0),
                diff_source=FakeTree().diff_source, reviewer=reviewer, chat=FakeChat(),
                store=ReviewLoopStore(tmp), wording=WORDING, options=FAST,
            )
            runner.start()
            runner.join(5)
        self.assertEqual(names, ['review-loop-T-7'])


class StoreRefusalTests(_Runs):

    def test_a_loop_keeps_running_when_its_state_cannot_be_saved(self) -> None:
        # Persistence is for the view; a disk that refuses every write must
        # not stop the review. The store's root is a FILE here, so every save
        # really fails.
        blocker = Path(tempfile.mkdtemp()) / 'not-a-folder'
        blocker.write_text('in the way')
        self.store = ReviewLoopStore(blocker)
        service = self.service(FakeChat(), FakeReviewer(reply()))
        self.start(service)
        self.assertEqual(self.finished(service).status, ReviewLoopStatus.CLEAN)
        self.assertFalse(self.store.save(service.state('T-1')))


if __name__ == '__main__':
    unittest.main()
