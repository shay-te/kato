"""The shared tick loop: ticks, survives a failing tick, stops at once."""

from __future__ import annotations

import threading
import time
import unittest

from utils_core_lib.utils_core_lib.tick_thread import TickThread


def _wait(predicate, timeout=5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


class TickThreadTests(unittest.TestCase):
    def test_ticks_until_stopped(self) -> None:
        calls = []
        thread = TickThread(lambda: calls.append(1), interval_seconds=0.01, name='t1')
        self.assertTrue(thread.start())
        self.assertFalse(thread.start())  # already running
        self.assertTrue(_wait(lambda: len(calls) >= 3))
        thread.stop()
        self.assertFalse(thread.running)
        count = len(calls)
        time.sleep(0.05)
        self.assertEqual(len(calls), count)

    def test_a_failing_tick_is_logged_and_the_next_one_runs(self) -> None:
        calls = []

        def tick():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError('first tick fails')

        thread = TickThread(tick, interval_seconds=0.01, name='t2')
        with self.assertLogs('t2', level='ERROR'):
            thread.start()
            self.assertTrue(_wait(lambda: len(calls) >= 2))
        thread.stop()

    def test_stop_does_not_wait_out_a_long_interval(self) -> None:
        started = threading.Event()
        thread = TickThread(started.set, interval_seconds=60, name='t3')
        thread.start()
        self.assertTrue(started.wait(5))
        began = time.monotonic()
        thread.stop()
        self.assertLess(time.monotonic() - began, 2)

    def test_stop_before_start_and_from_inside_a_tick(self) -> None:
        TickThread(lambda: None, interval_seconds=1, name='t4').stop()
        holder = {}

        def tick():
            holder['thread'].stop()  # must not try to join itself

        holder['thread'] = TickThread(tick, interval_seconds=0.01, name='t5')
        holder['thread'].start()
        self.assertTrue(_wait(lambda: not holder['thread'].running))

    def test_it_can_start_again_after_a_stop(self) -> None:
        calls = []
        thread = TickThread(lambda: calls.append(1), interval_seconds=0.01, name='t6')
        thread.start()
        thread.stop()
        self.assertTrue(thread.start())
        self.assertTrue(_wait(lambda: len(calls) >= 2))
        thread.stop()


if __name__ == '__main__':
    unittest.main()
