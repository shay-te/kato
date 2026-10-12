"""Best-effort work that must never hold the caller up.

The production failure behind this helper: a UI hint computed from the agent
blocked an operator's git action indefinitely because the agent was wedged.
The rule these tests encode is that the caller's deadline wins, always.
"""

from __future__ import annotations

import threading
import time
import unittest

from kato_core_lib.helpers.deadline import run_all_with_deadline, run_with_deadline


class RunWithDeadlineTests(unittest.TestCase):
    def test_a_fast_result_is_returned(self) -> None:
        self.assertEqual(run_with_deadline(lambda: 'value', seconds=5), 'value')

    def test_a_falsey_result_is_not_mistaken_for_a_timeout(self) -> None:
        self.assertIs(run_with_deadline(lambda: False, seconds=5, default=True), False)
        self.assertEqual(run_with_deadline(lambda: 0, seconds=5, default=9), 0)
        self.assertIsNone(run_with_deadline(lambda: None, seconds=5, default='x'))

    def test_slow_work_yields_the_default_without_waiting_for_it(self) -> None:
        started = time.monotonic()
        result = run_with_deadline(lambda: time.sleep(30), seconds=0.2, default='gave up')
        elapsed = time.monotonic() - started

        self.assertEqual(result, 'gave up')
        self.assertLess(elapsed, 5, 'the caller waited for work it had abandoned')

    def test_work_blocked_on_a_lock_still_returns_the_default(self) -> None:
        # The real shape: a thread stuck on a lock another thread holds.
        lock = threading.Lock()
        lock.acquire()
        self.addCleanup(lock.release)

        def _blocked():
            with lock:
                return 'never'

        self.assertEqual(
            run_with_deadline(_blocked, seconds=0.2, default='gave up'), 'gave up',
        )

    def test_a_raising_worker_yields_the_default_not_an_exception(self) -> None:
        # A best-effort value that failed is the same as one that never came;
        # raising would push the failure onto the caller this protects.
        def _boom():
            raise RuntimeError('probe exploded')

        self.assertEqual(run_with_deadline(_boom, seconds=5, default='safe'), 'safe')

    def test_the_timeout_callback_fires_only_on_a_timeout(self) -> None:
        fired = []
        run_with_deadline(lambda: 'fast', seconds=5, on_timeout=lambda: fired.append(1))
        self.assertEqual(fired, [])

        run_with_deadline(lambda: time.sleep(30), seconds=0.2,
                          on_timeout=lambda: fired.append(1))
        self.assertEqual(fired, [1])

    def test_a_raising_timeout_callback_does_not_reach_the_caller(self) -> None:
        def _bad_log():
            raise RuntimeError('logger down')

        self.assertEqual(
            run_with_deadline(lambda: time.sleep(30), seconds=0.2,
                              default='safe', on_timeout=_bad_log),
            'safe',
        )

    def test_a_zero_or_negative_deadline_does_not_wait(self) -> None:
        for seconds in (0, -1):
            with self.subTest(seconds=seconds):
                self.assertEqual(
                    run_with_deadline(lambda: time.sleep(30), seconds=seconds,
                                      default='gave up'),
                    'gave up',
                )

    def test_the_abandoned_worker_does_not_keep_the_process_alive(self) -> None:
        # Daemon threads only: an abandoned probe must never block shutdown.
        seen = {}

        def _slow():
            seen['daemon'] = threading.current_thread().daemon
            time.sleep(30)

        run_with_deadline(_slow, seconds=0.2, default=None)
        time.sleep(0.05)
        self.assertTrue(seen.get('daemon'), 'the probe thread was not a daemon')


class RunAllWithDeadlineTests(unittest.TestCase):

    def test_each_answer_comes_back_in_order(self) -> None:
        self.assertEqual(
            run_all_with_deadline([lambda: 1, lambda: 0, lambda: None], seconds=5, default='late'),
            [1, 0, None],
        )

    def test_one_slow_one_costs_only_its_own_answer_and_the_call_stays_bounded(self) -> None:
        release = threading.Event()
        self.addCleanup(release.set)
        started = time.monotonic()
        results = run_all_with_deadline(
            [lambda: 'fast', lambda: release.wait(10) and 'slow', lambda: 'also fast'],
            seconds=0.2, default='late',
        )
        self.assertEqual(results, ['fast', 'late', 'also fast'])
        self.assertLess(time.monotonic() - started, 2.0)

    def test_they_run_at_once_not_one_after_another(self) -> None:
        def slowish():
            time.sleep(0.3)
            return 'done'
        started = time.monotonic()
        self.assertEqual(run_all_with_deadline([slowish] * 5, seconds=5), ['done'] * 5)
        self.assertLess(time.monotonic() - started, 1.2)  # one after another: 1.5s

    def test_one_that_raises_is_the_default(self) -> None:
        def boom():
            raise RuntimeError('provider down')
        self.assertEqual(run_all_with_deadline([boom, lambda: 2], seconds=5, default='x'), ['x', 2])
        self.assertEqual(run_all_with_deadline([], seconds=5), [])


if __name__ == '__main__':
    unittest.main()
