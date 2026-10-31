from __future__ import annotations

import subprocess
import sys
import unittest
from unittest import mock

from lid_awake_core_lib.lid_awake_core_lib import mac_power
from lid_awake_core_lib.lid_awake_core_lib.decision import PowerState
from lid_awake_core_lib.lid_awake_core_lib.mac_power import (
    parse_power_state,
    read_heat_level,
    read_power_state,
)

ON_CHARGER = """Now drawing from 'AC Power'
 -InternalBattery-0 (id=38666339)\t100%; charged; 0:00 remaining present: true
"""
ON_BATTERY = """Now drawing from 'Battery Power'
 -InternalBattery-0 (id=38666339)\t57%; discharging; 4:12 remaining present: true
"""
DESKTOP_MAC = "Now drawing from 'AC Power'\n"


def _finished(stdout='', returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout, '')


class ParsePowerStateTests(unittest.TestCase):
    def test_on_the_charger(self):
        self.assertEqual(parse_power_state(ON_CHARGER), PowerState(True, 100))

    def test_on_battery(self):
        self.assertEqual(parse_power_state(ON_BATTERY), PowerState(False, 57))

    def test_a_desktop_mac_has_no_percent(self):
        self.assertEqual(parse_power_state(DESKTOP_MAC), PowerState(True, None))

    def test_output_it_cannot_read_is_unknown(self):
        self.assertEqual(parse_power_state('something else'), PowerState())


class ReadPowerStateTests(unittest.TestCase):
    def test_reads_pmset(self):
        with mock.patch.object(mac_power, 'run_command', return_value=_finished(ON_BATTERY)) as run:
            self.assertEqual(read_power_state(), PowerState(False, 57))
        run.assert_called_once_with(['/usr/bin/pmset', '-g', 'batt'])

    def test_a_failed_command_is_unknown(self):
        for finished in (None, _finished(returncode=1)):
            with mock.patch.object(mac_power, 'run_command', return_value=finished):
                self.assertEqual(read_power_state(), PowerState())


class ReadHeatLevelTests(unittest.TestCase):
    def test_reads_the_level(self):
        with mock.patch.object(mac_power, 'run_command', return_value=_finished('2\n')):
            self.assertEqual(read_heat_level(), 2)

    def test_a_failed_command_or_odd_output_is_unknown(self):
        for finished in (None, _finished(returncode=1), _finished('hot')):
            with mock.patch.object(mac_power, 'run_command', return_value=finished):
                self.assertIsNone(read_heat_level())


@unittest.skipUnless(sys.platform == 'darwin', 'reads the real Mac')
class RealMacTests(unittest.TestCase):
    def test_power_state_reads_this_mac(self):
        self.assertIsNotNone(read_power_state().on_charger)

    def test_heat_level_needs_no_root_and_is_in_range(self):
        self.assertIn(read_heat_level(), {0, 1, 2, 3})


if __name__ == '__main__':
    unittest.main()
