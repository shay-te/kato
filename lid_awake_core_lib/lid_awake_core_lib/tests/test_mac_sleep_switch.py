from __future__ import annotations

import subprocess
import sys
import unittest
from unittest import mock

from lid_awake_core_lib.lid_awake_core_lib import mac_sleep_switch
from lid_awake_core_lib.lid_awake_core_lib.mac_sleep_switch import (
    MacSleepSwitch,
    disable_sleep_command,
)


def _finished(stdout='', returncode=0, stderr=''):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


AWAKE = _finished('Currently in use:\n SleepDisabled\t\t1\n sleep 1\n')
NORMAL = _finished('Currently in use:\n sleep 1\n')
EXPLICIT_ZERO = _finished(' SleepDisabled\t\t0\n')


class IsHeldTests(unittest.TestCase):
    def _read(self, finished):
        with mock.patch.object(mac_sleep_switch, 'run_command', return_value=finished):
            return MacSleepSwitch().is_held()

    def test_on(self):
        self.assertIs(self._read(AWAKE), True)

    def test_macos_leaves_the_line_out_when_off(self):
        self.assertIs(self._read(NORMAL), False)

    def test_an_explicit_zero_is_off(self):
        self.assertIs(self._read(EXPLICIT_ZERO), False)

    def test_could_not_ask_is_none_never_false(self):
        self.assertIsNone(self._read(None))
        self.assertIsNone(self._read(_finished(returncode=1)))


class SetHeldTests(unittest.TestCase):
    def _set(self, disabled, *results):
        with mock.patch.object(mac_sleep_switch, 'run_command', side_effect=list(results)) as run:
            return MacSleepSwitch().set_held(disabled), run

    def test_runs_the_exact_command_without_a_prompt(self):
        error, run = self._set(True, _finished(), AWAKE)
        self.assertEqual(error, '')
        run.assert_any_call(['/usr/bin/sudo', '-n', '/usr/bin/pmset', '-a', 'disablesleep', '1'])

    def test_turning_sleep_back_on(self):
        error, run = self._set(False, _finished(), NORMAL)
        self.assertEqual(error, '')
        run.assert_any_call(['/usr/bin/sudo', '-n', '/usr/bin/pmset', '-a', 'disablesleep', '0'])

    def test_no_answer(self):
        self.assertEqual(self._set(True, None)[0], 'pmset did not answer')

    def test_a_refusal_carries_sudos_words(self):
        error, _ = self._set(True, _finished(returncode=1, stderr='a password is required\n'))
        self.assertEqual(error, 'pmset failed: a password is required')

    def test_a_silent_failure_still_says_something(self):
        self.assertEqual(self._set(True, _finished(returncode=3))[0], 'pmset failed: exit 3')

    def test_success_is_not_believed_until_read_back(self):
        self.assertEqual(self._set(True, _finished(), NORMAL)[0], 'pmset ran, but the Mac did not change')

    def test_an_unreadable_read_back_is_not_success(self):
        self.assertEqual(self._set(False, _finished(), None)[0], 'pmset ran, but the Mac did not change')


class CommandTests(unittest.TestCase):
    def test_both_directions(self):
        self.assertEqual(disable_sleep_command(True), ['/usr/bin/pmset', '-a', 'disablesleep', '1'])
        self.assertEqual(disable_sleep_command(False), ['/usr/bin/pmset', '-a', 'disablesleep', '0'])


@unittest.skipUnless(sys.platform == 'darwin', 'reads the real Mac')
class RealMacTests(unittest.TestCase):
    def test_can_read_this_mac(self):
        self.assertIn(MacSleepSwitch().is_held(), {True, False})


if __name__ == '__main__':
    unittest.main()
