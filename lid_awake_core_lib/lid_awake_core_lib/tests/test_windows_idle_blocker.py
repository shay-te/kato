from __future__ import annotations

import sys
import threading
import unittest

from lid_awake_core_lib.lid_awake_core_lib.windows_idle_blocker import (
    ES_CONTINUOUS,
    ES_SYSTEM_REQUIRED,
    WindowsIdleSleepBlocker,
)


class FakeState:
    """Records the calls, and from which thread: Windows ties them to it."""

    def __init__(self):
        self.calls = []
        self.threads = []
        self.called = threading.Event()

    def __call__(self, flags):
        self.calls.append(flags)
        self.threads.append(threading.current_thread())
        self.called.set()
        return 1


class FakeKernel32:
    def __init__(self):
        self.SetThreadExecutionState = FakeState()


class HoldTests(unittest.TestCase):
    def setUp(self):
        self.kernel32 = FakeKernel32()
        self.blocker = WindowsIdleSleepBlocker(kernel32=self.kernel32)
        self.addCleanup(self.blocker.release)

    def _held(self):
        self.blocker.hold()
        self.assertTrue(self.kernel32.SetThreadExecutionState.called.wait(5))

    def test_asks_windows_to_stay_awake(self):
        self._held()
        self.assertEqual(
            self.kernel32.SetThreadExecutionState.calls,
            [ES_CONTINUOUS | ES_SYSTEM_REQUIRED],
        )
        self.assertTrue(self.blocker.holding)

    def test_a_second_hold_keeps_the_same_thread(self):
        self._held()
        first = self.blocker._thread
        self.blocker.hold()
        self.assertIs(self.blocker._thread, first)

    def test_release_clears_it_on_the_same_thread_it_was_set_on(self):
        self._held()
        self.blocker.release()
        state = self.kernel32.SetThreadExecutionState
        self.assertEqual(state.calls[-1], ES_CONTINUOUS)
        self.assertIs(state.threads[0], state.threads[1])
        self.assertIsNot(state.threads[0], threading.current_thread())
        self.assertFalse(self.blocker.holding)

    def test_hold_works_again_after_a_release(self):
        self._held()
        self.blocker.release()
        self.kernel32.SetThreadExecutionState.called.clear()
        self._held()
        self.assertTrue(self.blocker.holding)

    def test_releasing_nothing_does_nothing(self):
        WindowsIdleSleepBlocker(kernel32=self.kernel32).release()
        self.assertEqual(self.kernel32.SetThreadExecutionState.calls, [])


@unittest.skipUnless(sys.platform == 'win32', 'calls the real Windows API')
class RealWindowsTests(unittest.TestCase):
    def test_hold_and_release(self):
        blocker = WindowsIdleSleepBlocker()
        blocker.hold()
        self.assertTrue(blocker.holding)
        blocker.release()
        self.assertFalse(blocker.holding)


if __name__ == '__main__':
    unittest.main()
