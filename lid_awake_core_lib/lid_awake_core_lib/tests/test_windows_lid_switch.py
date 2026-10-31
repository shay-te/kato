from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lid_awake_core_lib.lid_awake_core_lib import windows_lid_switch
from lid_awake_core_lib.lid_awake_core_lib.windows_lid_switch import (
    WindowsLidSwitch,
    parse_lid_actions,
)

ENGLISH = """Power Scheme GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (Balanced)
  GUID Alias: SCHEME_BALANCED
  Subgroup GUID: 4f971e89-eebd-4455-a8de-9e59040e7347  (Power buttons and lid)
    GUID Alias: SUB_BUTTONS
    Power Setting GUID: 5ca83367-6e45-459f-a27b-476b1d01c936  (Lid close action)
      GUID Alias: LIDACTION
      Possible Setting Index: 000
      Possible Setting Friendly Name: Do nothing
      Possible Setting Index: 001
      Possible Setting Friendly Name: Sleep
    Current AC Power Setting Index: 0x00000001
    Current DC Power Setting Index: 0x00000002
"""
GERMAN = ENGLISH.replace(
    'Current AC Power Setting Index', 'Index der aktuellen Wechselstromeinstellung',
).replace(
    'Current DC Power Setting Index', 'Index der aktuellen Gleichstromeinstellung',
)


class ParseTests(unittest.TestCase):
    def test_reads_ac_then_dc(self):
        self.assertEqual(parse_lid_actions(ENGLISH), (1, 2))

    def test_does_not_depend_on_the_translated_labels(self):
        self.assertEqual(parse_lid_actions(GERMAN), (1, 2))

    def test_unreadable(self):
        self.assertIsNone(parse_lid_actions('The specified setting does not exist.'))


class FakePowercfg:
    """A stand-in for ``powercfg`` that really keeps the two values."""

    def __init__(self, ac=1, dc=2):
        self.ac, self.dc = ac, dc
        self.fail_on = ''
        self.ignore_changes = False
        self.silent = False
        self.calls = []

    def __call__(self, argv):
        self.calls.append(argv)
        verb = argv[1]
        if self.silent:
            return None
        if verb == self.fail_on:
            return subprocess.CompletedProcess(argv, 1, '', 'Access is denied.\n')
        if verb == '/query':
            text = f'Current AC: 0x{self.ac:08x}\nCurrent DC: 0x{self.dc:08x}\n'
            return subprocess.CompletedProcess(argv, 0, text, '')
        if not self.ignore_changes:
            if verb == '/setacvalueindex':
                self.ac = int(argv[-1])
            if verb == '/setdcvalueindex':
                self.dc = int(argv[-1])
        return subprocess.CompletedProcess(argv, 0, '', '')


class SwitchTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.saved = Path(folder.name) / 'lid.json'
        self.powercfg = FakePowercfg()
        patcher = mock.patch.object(windows_lid_switch, 'run_command', self.powercfg)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.start_watchdog = mock.Mock()
        self.switch = WindowsLidSwitch(saved=self.saved, start_watchdog=self.start_watchdog)

    def test_holding_sets_do_nothing_on_both_and_activates_the_plan(self):
        self.assertEqual(self.switch.set_held(True), '')
        self.assertEqual((self.powercfg.ac, self.powercfg.dc), (0, 0))
        self.assertIn(['powercfg', '/setactive', 'SCHEME_CURRENT'], self.powercfg.calls)
        self.assertIs(self.switch.is_held(), True)

    def test_the_operators_values_are_kept_before_anything_changes(self):
        self.switch.set_held(True)
        self.assertEqual(json.loads(self.saved.read_text()), {'ac': 1, 'dc': 2})

    def test_a_second_hold_does_not_overwrite_them_with_do_nothing(self):
        self.switch.set_held(True)
        self.switch.set_held(True)
        self.assertEqual(json.loads(self.saved.read_text()), {'ac': 1, 'dc': 2})

    def test_the_watchdog_starts_once_the_hold_is_confirmed(self):
        self.switch.set_held(True)
        self.start_watchdog.assert_called_once_with()

    def test_no_watchdog_when_the_hold_failed(self):
        self.powercfg.fail_on = '/setacvalueindex'
        self.assertIn('Access is denied', self.switch.set_held(True))
        self.start_watchdog.assert_not_called()

    def test_releasing_puts_the_operators_values_back(self):
        self.switch.set_held(True)
        self.assertEqual(self.switch.set_held(False), '')
        self.assertEqual((self.powercfg.ac, self.powercfg.dc), (1, 2))
        self.assertFalse(self.saved.exists())
        self.assertIs(self.switch.is_held(), False)

    def test_a_failed_release_keeps_the_saved_values(self):
        self.switch.set_held(True)
        self.powercfg.fail_on = '/setdcvalueindex'
        self.assertIn('Access is denied', self.switch.set_held(False))
        self.assertTrue(self.saved.exists())

    def test_a_plan_locked_by_group_policy_is_caught_by_the_read_back(self):
        self.powercfg.ignore_changes = True
        self.assertEqual(
            self.switch.set_held(True), 'powercfg ran, but the computer did not change',
        )

    def test_an_operator_who_already_chose_do_nothing_is_not_held(self):
        self.powercfg.ac = self.powercfg.dc = 0
        self.assertIs(self.switch.is_held(), False)

    def test_their_own_do_nothing_survives_a_hold_and_release(self):
        self.powercfg.ac = self.powercfg.dc = 0
        self.switch.set_held(True)
        self.switch.set_held(False)
        self.assertEqual((self.powercfg.ac, self.powercfg.dc), (0, 0))

    def test_could_not_ask_is_none_never_false(self):
        self.powercfg.silent = True
        self.assertIsNone(self.switch.is_held())

    def test_powercfg_silent_on_hold(self):
        self.powercfg.silent = True
        self.assertEqual(self.switch.set_held(True), 'powercfg did not answer')

    def test_powercfg_dying_midway(self):
        real = self.powercfg.__call__
        answers = iter([real(['powercfg', '/query']), None])
        with mock.patch.object(windows_lid_switch, 'run_command', lambda argv: next(answers)):
            self.assertEqual(self.switch.set_held(True), 'powercfg did not answer')

    def test_a_failing_query_is_unknown(self):
        self.powercfg.fail_on = '/query'
        self.assertIsNone(self.switch.is_held())

    def test_a_refusal_with_no_words_still_says_something(self):
        def refuse(argv):
            if argv[1] == '/query':
                return self.powercfg(argv)
            return subprocess.CompletedProcess(argv, 5, '', '')

        with mock.patch.object(windows_lid_switch, 'run_command', refuse):
            self.assertEqual(self.switch.set_held(True), 'powercfg failed: 5')

    def test_nothing_saved_means_nothing_to_put_back(self):
        self.assertEqual(self.switch.set_held(False), '')
        self.assertEqual(self.powercfg.calls, [])

    def test_a_corrupt_saved_file_never_writes_junk_to_the_plan(self):
        self.saved.write_text('not json')
        self.assertEqual(self.switch.set_held(False), '')
        self.assertEqual(self.powercfg.calls, [])


@unittest.skipUnless(sys.platform == 'win32', 'asks the real powercfg')
class RealWindowsTests(unittest.TestCase):
    def test_the_query_can_be_read_or_says_it_cannot(self):
        with tempfile.TemporaryDirectory() as folder:
            switch = WindowsLidSwitch(saved=Path(folder) / 's', start_watchdog=mock.Mock())
            self.assertIn(switch.is_held(), {True, False, None})


if __name__ == '__main__':
    unittest.main()
