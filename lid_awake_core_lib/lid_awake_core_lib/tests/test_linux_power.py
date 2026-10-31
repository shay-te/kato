from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from lid_awake_core_lib.lid_awake_core_lib import linux_power
from lid_awake_core_lib.lid_awake_core_lib.decision import (
    HEAT_LEVEL_NO_SENSOR,
    PowerState,
)
from lid_awake_core_lib.lid_awake_core_lib.linux_power import (
    heat_level_for,
    read_heat_level,
    read_power_state,
)


class SysTestCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def _write(self, name, **files):
        directory = self.root / name
        directory.mkdir()
        for key, value in files.items():
            (directory / key).write_text(f'{value}\n')


class PowerTests(SysTestCase):
    def test_a_desktop_has_no_battery_and_counts_as_on_the_charger(self):
        self.assertEqual(read_power_state(self.root), PowerState(on_charger=True))

    def test_unplugged(self):
        self._write('AC', type='Mains', online=0)
        self._write('BAT0', type='Battery', capacity=64, status='Discharging')
        self.assertEqual(read_power_state(self.root), PowerState(False, 64))

    def test_plugged_in(self):
        self._write('AC', type='Mains', online=1)
        self._write('BAT0', type='Battery', capacity=64, status='Charging')
        self.assertEqual(read_power_state(self.root), PowerState(True, 64))

    def test_a_laptop_with_no_mains_entry_goes_by_the_battery(self):
        self._write('BAT0', type='Battery', capacity=99, status='Full')
        self.assertEqual(read_power_state(self.root), PowerState(True, 99))

    def test_usb_c_charging_counts(self):
        self._write('ucsi-source-psy', type='USB', online=1)
        self._write('BAT1', type='Battery', capacity=50, status='Discharging')
        self.assertTrue(read_power_state(self.root).on_charger)

    def test_an_unreadable_capacity_is_unknown(self):
        self._write('BAT0', type='Battery', status='Discharging')
        self.assertEqual(read_power_state(self.root), PowerState(False, None))


class HeatTests(SysTestCase):
    def test_no_sensor(self):
        self.assertEqual(read_heat_level(self.root), HEAT_LEVEL_NO_SENSOR)

    def test_the_hottest_zone_decides(self):
        self._write('thermal_zone0', temp=45000)
        self._write('thermal_zone1', temp=92000)
        self.assertEqual(read_heat_level(self.root), 2)

    def test_junk_is_ignored(self):
        self._write('thermal_zone0', temp='n/a')
        self.assertEqual(read_heat_level(self.root), HEAT_LEVEL_NO_SENSOR)

    def test_levels(self):
        for celsius, level in ((40, 0), (79.9, 0), (80, 1), (89.9, 1), (90, 2), (100, 3)):
            self.assertEqual(heat_level_for(celsius), level, celsius)


@unittest.skipUnless(sys.platform.startswith('linux'), 'reads a real /sys')
class RealLinuxTests(unittest.TestCase):
    def test_reads_this_machine(self):
        self.assertIsNotNone(linux_power.read_power_state().on_charger)
        self.assertIn(linux_power.read_heat_level(), {HEAT_LEVEL_NO_SENSOR, 0, 1, 2, 3})


if __name__ == '__main__':
    unittest.main()
