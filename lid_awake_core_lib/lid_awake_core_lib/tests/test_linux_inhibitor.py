from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from lid_awake_core_lib.lid_awake_core_lib import linux_inhibitor
from lid_awake_core_lib.lid_awake_core_lib.linux_inhibitor import (
    WHAT_IDLE_ONLY,
    WHAT_LID_CLOSED,
    LinuxInhibitor,
)
from lid_awake_core_lib.lid_awake_core_lib.mac_crash_guard import STALE_AFTER_MINUTES


def _inhibitor(**overrides):
    values = {'what': WHAT_LID_CLOSED, 'who': 'host', 'why': 'working', 'owner_pid': 42,
              'settle_seconds': 0}
    return LinuxInhibitor(**{**values, **overrides})


class CommandTests(unittest.TestCase):
    def test_blocks_the_lid_and_sleep(self):
        command = _inhibitor(heartbeat='/h/beat').command()
        self.assertEqual(command[:5], [
            'systemd-inhibit', '--what=handle-lid-switch:sleep:idle', '--who=host',
            '--why=working', '--mode=block',
        ])
        self.assertEqual(command[-3:], ['42', '/h/beat', '30'])

    def test_the_idle_hold_leaves_the_lid_alone(self):
        self.assertNotIn('handle-lid-switch', WHAT_IDLE_ONLY)

    def test_defaults_to_this_process(self):
        with mock.patch('os.getpid', return_value=7):
            inhibitor = LinuxInhibitor(what='x', who='h', why='w')
        self.assertEqual(inhibitor.command()[-3], '7')


class SpawnTests(unittest.TestCase):
    def test_a_refusal_carries_its_reason_and_holds_nothing(self):
        process = mock.Mock()
        process.poll.return_value = 1
        process.communicate.return_value = ('', 'Access denied\n')
        inhibitor = _inhibitor(spawn=mock.Mock(return_value=process))
        self.assertEqual(inhibitor.set_held(True), 'systemd-inhibit refused: Access denied')
        self.assertFalse(inhibitor.is_held())

    def test_a_silent_refusal_still_says_something(self):
        process = mock.Mock()
        process.poll.return_value = 1
        process.communicate.return_value = ('', '')
        inhibitor = _inhibitor(spawn=mock.Mock(return_value=process))
        self.assertIn('no reason given', inhibitor.set_held(True))

    def test_a_missing_binary_is_an_error_not_a_crash(self):
        inhibitor = _inhibitor(spawn=mock.Mock(side_effect=OSError('not found')))
        self.assertIn('could not start: not found', inhibitor.set_held(True))
        self.assertFalse(inhibitor.holding)

    def test_hold_is_safe_to_repeat(self):
        process = mock.Mock()
        process.poll.return_value = None
        spawn = mock.Mock(return_value=process)
        inhibitor = _inhibitor(spawn=spawn)
        inhibitor.hold()
        inhibitor.hold()
        spawn.assert_called_once()

    def test_one_that_ignores_terminate_is_killed(self):
        process = mock.Mock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired('x', 2), 0]
        inhibitor = _inhibitor(spawn=mock.Mock(return_value=process))
        inhibitor.hold()
        inhibitor.release()
        process.kill.assert_called_once_with()

    def test_releasing_nothing_or_a_dead_process_does_nothing(self):
        _inhibitor().release()
        process = mock.Mock()
        process.poll.side_effect = [None, 0]
        inhibitor = _inhibitor(spawn=mock.Mock(return_value=process))
        inhibitor.hold()
        inhibitor.release()
        process.terminate.assert_not_called()


@unittest.skipIf(sys.platform == 'win32', 'runs a real POSIX shell')
class RealWatchLoopTests(unittest.TestCase):
    """The real loop in a real shell. Only ``systemd-inhibit`` itself is faked:
    a stand-in that drops its flags and runs the command, as the real one does."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.binary = self.root / 'systemd-inhibit'
        self.binary.write_text(
            '#!/bin/sh\n'
            'while [ "${1#--}" != "$1" ]; do shift; done\n'
            'exec "$@"\n'
        )
        self.binary.chmod(self.binary.stat().st_mode | stat.S_IEXEC)
        self.heartbeat = self.root / 'heartbeat'
        self.heartbeat.touch()
        self.owner = subprocess.Popen(
            [sys.executable, '-c', 'import time; time.sleep(60)'],
            stdin=subprocess.DEVNULL,
        )
        self.addCleanup(self.owner.wait)
        self.addCleanup(self.owner.kill)
        self.inhibitor = LinuxInhibitor(
            what=WHAT_LID_CLOSED, who='h', why='w',
            heartbeat=self.heartbeat,
            owner_pid=self.owner.pid,
            binary=str(self.binary),
            check_seconds=0.05,
            settle_seconds=0.15,
        )
        self.addCleanup(self.inhibitor.release)

    def _ends_soon(self):
        deadline = time.monotonic() + 5
        while self.inhibitor.is_held() and time.monotonic() < deadline:
            time.sleep(0.02)
        return not self.inhibitor.is_held()

    def test_holds_while_the_owner_lives_and_the_heartbeat_is_fresh(self):
        self.assertEqual(self.inhibitor.set_held(True), '')
        time.sleep(0.3)
        self.assertTrue(self.inhibitor.is_held())

    def test_set_held_false_ends_it(self):
        self.inhibitor.set_held(True)
        self.assertEqual(self.inhibitor.set_held(False), '')
        self.assertFalse(self.inhibitor.is_held())

    def test_it_ends_by_itself_when_the_owner_crashes(self):
        self.inhibitor.set_held(True)
        self.owner.kill()
        self.owner.wait()
        self.assertTrue(self._ends_soon())

    def test_it_ends_by_itself_when_a_hung_owner_stops_beating(self):
        self.inhibitor.set_held(True)
        when = time.time() - (STALE_AFTER_MINUTES + 2) * 60
        os.utime(self.heartbeat, (when, when))
        self.assertTrue(self._ends_soon())

    def test_the_idle_hold_needs_no_heartbeat(self):
        inhibitor = LinuxInhibitor(
            what=WHAT_IDLE_ONLY, who='h', why='w', owner_pid=self.owner.pid,
            binary=str(self.binary), check_seconds=0.05, settle_seconds=0.15,
        )
        self.addCleanup(inhibitor.release)
        inhibitor.hold()
        self.assertTrue(inhibitor.holding)


class ConstantsTests(unittest.TestCase):
    def test_the_binary_name(self):
        self.assertEqual(linux_inhibitor.SYSTEMD_INHIBIT, 'systemd-inhibit')


if __name__ == '__main__':
    unittest.main()
