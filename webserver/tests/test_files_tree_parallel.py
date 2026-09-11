"""The Files-tree fan-out: concurrent, and ORDER-PRESERVING.

Building one repo's entry costs four git/disk passes (branch, tracked tree,
conflicts, changed set). A 25-repo workspace ran ~100 subprocesses strictly in
series and the pane sat empty for seconds — "it loaded now but took lots of
time".

Order is not a detail: the pane renders repos in the order the task lists
them, so a fan-out returning them in completion order would shuffle the pane
on every load.
"""

from __future__ import annotations

import random
import threading
import time
import unittest

from webserver.kato_webserver.app import build_in_parallel


class BuildInParallelTests(unittest.TestCase):
    def test_results_keep_the_input_order(self) -> None:
        # Deliberately finish in REVERSE: the slowest item is first, so an
        # append-as-they-complete implementation returns the exact opposite.
        items = list(range(8))

        def build(i):
            time.sleep((8 - i) * 0.01)
            return f'repo-{i}'

        self.assertEqual(
            build_in_parallel(items, build),
            [f'repo-{i}' for i in items],
        )

    def test_it_actually_runs_concurrently(self) -> None:
        # 8 × 50ms is 400ms in series.
        started = time.monotonic()
        build_in_parallel(range(8), lambda _i: time.sleep(0.05) or 'x')
        self.assertLess(time.monotonic() - started, 0.3)

    def test_concurrency_is_bounded(self) -> None:
        # The disk is the limit past a handful, so this must not become one
        # thread per repo on a 25-repo task.
        peak = 0
        live = 0
        lock = threading.Lock()

        def build(_i):
            nonlocal peak, live
            with lock:
                live += 1
                peak = max(peak, live)
            time.sleep(0.02)
            with lock:
                live -= 1
            return 'x'

        build_in_parallel(range(40), build, max_workers=4)
        self.assertLessEqual(peak, 4)

    def test_none_entries_are_dropped_but_order_survives(self) -> None:
        # A repo with no workspace path contributes nothing; the rest must
        # still line up.
        def build(i):
            return None if i % 2 else f'keep-{i}'

        self.assertEqual(
            build_in_parallel(range(6), build),
            ['keep-0', 'keep-2', 'keep-4'],
        )

    def test_an_empty_input_does_no_work(self) -> None:
        self.assertEqual(build_in_parallel([], lambda _i: 'x'), [])
        self.assertEqual(build_in_parallel(None, lambda _i: 'x'), [])

    def test_a_failing_item_still_surfaces(self) -> None:
        # Swallowing it would hand the operator a silently incomplete tree,
        # which is how a missing repo goes unnoticed.
        def build(i):
            if i == 3:
                raise RuntimeError('git exploded')
            return 'x'

        with self.assertRaises(RuntimeError):
            build_in_parallel(range(6), build)

    def test_order_holds_under_random_timing(self) -> None:
        rng = random.Random(1234)
        delays = [rng.random() * 0.02 for _ in range(12)]

        def build(i):
            time.sleep(delays[i])
            return i

        self.assertEqual(build_in_parallel(range(12), build), list(range(12)))


if __name__ == '__main__':
    unittest.main()
