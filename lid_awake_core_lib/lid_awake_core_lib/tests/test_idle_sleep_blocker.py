from __future__ import annotations

import shutil
import subprocess
import sys
import time
import unittest
from unittest import mock

from lid_awake_core_lib.lid_awake_core_lib.idle_sleep_blocker import (
    CAFFEINATE,
    IdleSleepBlocker,
    caffeinate_command,
)


class HoldTests(unittest.TestCase):
    def test_spawns_tied_to_the_owner_with_no_terminal(self):
        process = mock.Mock()
        process.poll.return_value = None
        spawn = mock.Mock(return_value=process)
        blocker = IdleSleepBlocker(owner_pid=4242, spawn=spawn)
        blocker.hold()
        spawn.assert_called_once_with(
            [CAFFEINATE, '-i', '-w', '4242'],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self.assertTrue(blocker.holding)

    def test_defaults_to_this_process(self):
        spawn = mock.Mock()
        with mock.patch('os.getpid', return_value=77):
            IdleSleepBlocker(spawn=spawn).hold()
        self.assertEqual(spawn.call_args.args[0], caffeinate_command(77))

    def test_a_second_hold_keeps_the_same_process(self):
        process = mock.Mock()
        process.poll.return_value = None
        spawn = mock.Mock(return_value=process)
        blocker = IdleSleepBlocker(owner_pid=1, spawn=spawn)
        blocker.hold()
        blocker.hold()
        spawn.assert_called_once()

    def test_a_dead_process_is_replaced(self):
        dead = mock.Mock()
        dead.poll.return_value = 0
        spawn = mock.Mock(return_value=dead)
        blocker = IdleSleepBlocker(owner_pid=1, spawn=spawn)
        blocker.hold()
        self.assertFalse(blocker.holding)
        blocker.hold()
        self.assertEqual(spawn.call_count, 2)

    def test_no_caffeinate_binary_is_not_a_crash(self):
        blocker = IdleSleepBlocker(owner_pid=1, spawn=mock.Mock(side_effect=OSError()))
        blocker.hold()
        self.assertFalse(blocker.holding)


class ReleaseTests(unittest.TestCase):
    def _held(self, process):
        blocker = IdleSleepBlocker(owner_pid=1, spawn=mock.Mock(return_value=process))
        blocker.hold()
        return blocker

    def test_with_nothing_held_does_nothing(self):
        IdleSleepBlocker(owner_pid=1, spawn=mock.Mock()).release()

    def test_terminates_and_waits(self):
        process = mock.Mock()
        process.poll.return_value = None
        self._held(process).release()
        process.terminate.assert_called_once_with()
        process.wait.assert_called_once()
        process.kill.assert_not_called()

    def test_an_already_dead_process_is_left_alone(self):
        process = mock.Mock()
        process.poll.return_value = 0
        self._held(process).release()
        process.terminate.assert_not_called()

    def test_one_that_ignores_terminate_is_killed(self):
        process = mock.Mock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired('caffeinate', 2), 0]
        self._held(process).release()
        process.kill.assert_called_once_with()


@unittest.skipUnless(shutil.which('caffeinate'), 'needs the real caffeinate')
class RealCaffeinateTests(unittest.TestCase):
    def setUp(self):
        self.owner = subprocess.Popen(
            [sys.executable, '-c', 'import time; time.sleep(60)'],
            stdin=subprocess.DEVNULL,
        )
        self.addCleanup(self.owner.wait)
        self.addCleanup(self.owner.kill)
        self.blocker = IdleSleepBlocker(owner_pid=self.owner.pid)
        self.addCleanup(self.blocker.release)

    def _wait_until_not_holding(self):
        deadline = time.monotonic() + 5
        while self.blocker.holding and time.monotonic() < deadline:
            time.sleep(0.05)

    def test_hold_and_release(self):
        self.blocker.hold()
        self.assertTrue(self.blocker.holding)
        self.blocker.release()
        self.assertFalse(self.blocker.holding)

    def test_it_dies_with_its_owner_even_with_no_release(self):
        self.blocker.hold()
        self.assertTrue(self.blocker.holding)
        self.owner.kill()
        self.owner.wait()
        self._wait_until_not_holding()
        self.assertFalse(self.blocker.holding)


if __name__ == '__main__':
    unittest.main()
