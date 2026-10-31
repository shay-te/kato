from __future__ import annotations

import ctypes
import sys
import unittest

from lid_awake_core_lib.lid_awake_core_lib.decision import (
    HEAT_LEVEL_NO_SENSOR,
    PowerState,
)
from lid_awake_core_lib.lid_awake_core_lib.windows_power import (
    SystemPowerStatus,
    power_state_from,
    read_heat_level,
    read_power_state,
)


class FakeKernel32:
    def __init__(self, *, ok=True, ac=1, percent=80):
        self.ok, self.ac, self.percent = ok, ac, percent

    def GetSystemPowerStatus(self, pointer):
        pointer._obj.ACLineStatus = self.ac
        pointer._obj.BatteryLifePercent = self.percent
        return self.ok


class PowerStateFromTests(unittest.TestCase):
    def test_plugged_in(self):
        self.assertEqual(power_state_from(1, 80), PowerState(True, 80))

    def test_unplugged(self):
        self.assertEqual(power_state_from(0, 42), PowerState(False, 42))

    def test_windows_says_unknown_with_255(self):
        self.assertEqual(power_state_from(255, 255), PowerState(None, None))

    def test_a_desktop_has_no_percent(self):
        self.assertEqual(power_state_from(1, 255), PowerState(True, None))


class ReadPowerStateTests(unittest.TestCase):
    def test_reads_the_status(self):
        self.assertEqual(
            read_power_state(FakeKernel32(ac=0, percent=33)), PowerState(False, 33),
        )

    def test_a_failed_call_is_unknown(self):
        self.assertEqual(read_power_state(FakeKernel32(ok=False)), PowerState())

    def test_the_structure_matches_the_windows_layout(self):
        # Four single bytes, then two DWORDs: 12 bytes, on every platform.
        self.assertEqual(ctypes.sizeof(SystemPowerStatus), 12)


class HeatTests(unittest.TestCase):
    def test_says_no_sensor_rather_than_guessing(self):
        self.assertEqual(read_heat_level(), HEAT_LEVEL_NO_SENSOR)


@unittest.skipUnless(sys.platform == 'win32', 'calls the real Windows API')
class RealWindowsTests(unittest.TestCase):
    def test_reads_this_machine(self):
        state = read_power_state()
        self.assertIn(state.on_charger, {True, False, None})


if __name__ == '__main__':
    unittest.main()
