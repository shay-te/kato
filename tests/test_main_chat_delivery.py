"""``MainChatDelivery``: the one way kato puts a message into a task's chat.

Its busy / stalled / send-or-respawn behaviour came out of the comment-run
service unchanged and keeps that service's tests. What is new is what a SECOND
sender needs: knowing when the chat's turn ended (without consuming the live
event queue), and the guarantee that two senders reaching the same idle moment
can never both send.
"""
from __future__ import annotations

import threading
import time
import unittest
from types import SimpleNamespace
import logging

from kato_core_lib.data_layers.service.main_chat_delivery import (
    MainChatDelivery,
    TurnEnd,
)
from kato_core_lib.data_layers.service.task_comment_run_service import (
    TaskCommentRunService,
)


class _Event(object):
    def __init__(self, event_type: str, received_at: float, **raw) -> None:
        self.raw = {'type': event_type, **raw}
        self.received_at_epoch = received_at

    @property
    def event_type(self) -> str:
        return self.raw['type']

    @property
    def is_terminal(self) -> bool:
        return self.event_type in ('result', 'turn.completed', 'turn.failed', 'turn.aborted')


class _Session(object):
    """A live chat: counts sends, answers ``recent_events`` without consuming."""

    def __init__(self, events=None) -> None:
        self.is_alive = True
        self.is_working = False
        self.user_messages_sent = 0
        self.result_events_received = 0
        self.sent: list[str] = []
        self._events = list(events or [])
        self._lock = threading.Lock()

    def recent_events(self):
        return list(self._events)

    def send_user_message(self, prompt: str) -> None:
        with self._lock:
            # A small gap between "checked idle" and "counted the send" is
            # exactly the window two unsynchronised senders would both use.
            time.sleep(0.01)
            self.sent.append(prompt)
            self.user_messages_sent += 1


def _delivery_for(session) -> MainChatDelivery:
    manager = SimpleNamespace(get_session=lambda _task_id: session)
    return MainChatDelivery(session_manager=manager, logger=logging.getLogger('test.chat_delivery'))


class TurnEndSinceTests(unittest.TestCase):

    def test_the_newest_finished_turn_after_the_send(self) -> None:
        session = _Session([
            _Event('result', 100.0, result='old turn'),
            _Event('assistant', 150.0),
            _Event('result', 200.0, result='fixed it', is_error=False),
        ])
        turn = _delivery_for(session).turn_end_since('T1', 120.0)
        self.assertEqual(turn, TurnEnd(received_at=200.0, is_error=False, text='fixed it'))

    def test_nothing_finished_since_the_send(self) -> None:
        session = _Session([_Event('result', 100.0), _Event('assistant', 150.0)])
        self.assertIsNone(_delivery_for(session).turn_end_since('T1', 120.0))

    def test_an_error_result_is_reported_as_an_error(self) -> None:
        session = _Session([_Event('result', 200.0, is_error=True, result='boom')])
        self.assertTrue(_delivery_for(session).turn_end_since('T1', 0).is_error)

    def test_a_failed_codex_turn_is_an_error_and_a_completed_one_is_not(self) -> None:
        failed = _Session([_Event('turn.failed', 200.0)])
        done = _Session([_Event('turn.completed', 200.0)])
        self.assertTrue(_delivery_for(failed).turn_end_since('T1', 0).is_error)
        self.assertFalse(_delivery_for(done).turn_end_since('T1', 0).is_error)

    def test_it_never_consumes_the_live_event_queue(self) -> None:
        session = _Session([_Event('result', 200.0)])
        consumed: list = []
        session.poll_event = lambda *args: consumed.append(args)
        _delivery_for(session).turn_end_since('T1', 0)
        self.assertEqual(consumed, [])

    def test_no_session_or_a_broken_one_means_no_turn(self) -> None:
        self.assertIsNone(MainChatDelivery().turn_end_since('T1', 0))
        self.assertIsNone(_delivery_for(None).turn_end_since('T1', 0))
        broken = _Session()

        def gone():
            raise RuntimeError('the session died mid-read')

        broken.recent_events = gone
        self.assertIsNone(_delivery_for(broken).turn_end_since('T1', 0))


class OneSenderAtATimeTests(unittest.TestCase):

    def test_two_senders_on_the_same_idle_moment_send_once(self) -> None:
        # The check-then-send each sender does is only atomic because every
        # sender holds the SAME per-task lock across it.
        session = _Session()
        delivery = _delivery_for(session)
        go = threading.Barrier(2)

        def sender(name: str) -> None:
            go.wait()
            with delivery.dispatch_lock_for('T1'):
                if not delivery.has_busy_turn('T1'):
                    delivery.deliver('T1', name, label=name, cwd_for=lambda: '')

        threads = [threading.Thread(target=sender, args=(n,)) for n in ('comment', 'review')]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        self.assertEqual(len(session.sent), 1)

    def test_one_lock_per_task(self) -> None:
        delivery = MainChatDelivery()
        self.assertIs(delivery.dispatch_lock_for('T1'), delivery.dispatch_lock_for('T1'))
        self.assertIsNot(delivery.dispatch_lock_for('T1'), delivery.dispatch_lock_for('T2'))

    def test_the_comment_runs_share_their_sender(self) -> None:
        # Anything else that writes into a task's chat must use THIS instance,
        # or it would hold a different lock than the comment drain.
        service = TaskCommentRunService(comment_service=None)
        self.assertIsInstance(service.chat_delivery, MainChatDelivery)
        self.assertIs(service.chat_delivery, service.chat_delivery)


class RunnerInFlightTests(unittest.TestCase):

    def test_reports_the_real_parallel_runner(self) -> None:
        from kato_core_lib.data_layers.service.parallel_task_runner import (
            ParallelTaskRunner,
        )
        runner = ParallelTaskRunner(max_workers=1)
        self.addCleanup(runner.shutdown, wait=True)
        release = threading.Event()
        runner.submit('T1', lambda: release.wait(5))
        delivery = MainChatDelivery(parallel_task_runner=runner)
        self.assertTrue(delivery.runner_in_flight('T1'))
        self.assertFalse(delivery.runner_in_flight('T2'))
        release.set()
        deadline = time.monotonic() + 5
        while delivery.runner_in_flight('T1') and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(delivery.runner_in_flight('T1'))

    def test_no_runner_or_a_failing_one_is_not_in_flight(self) -> None:
        class _Broken(object):
            def is_in_flight(self, task_id):
                raise RuntimeError('runner shut down')

        self.assertFalse(MainChatDelivery().runner_in_flight('T1'))
        self.assertFalse(MainChatDelivery(parallel_task_runner=_Broken()).runner_in_flight('T1'))


if __name__ == '__main__':
    unittest.main()
