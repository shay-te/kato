from __future__ import annotations

import unittest

from lid_awake_core_lib.lid_awake_core_lib.decision import (
    BATTERY_FLOOR_PERCENT,
    HEAT_LEVEL_NO_SENSOR,
    HEAT_LEVEL_TOO_HOT,
    KEEP_AWAKE_REASONS,
    LidAwakeInputs,
    LidAwakeReason,
    PowerState,
    blocks_idle_sleep,
    decide,
)

CHARGER = PowerState(on_charger=True, battery_percent=100)
DESKTOP_MAC = PowerState(on_charger=True, battery_percent=None)
BATTERY = PowerState(on_charger=False, battery_percent=80)
READY = {'enabled': True, 'supported': True, 'guard_ready': True, 'heat_level': 0}


class DecideTests(unittest.TestCase):
    def test_off_outranks_everything(self):
        self.assertEqual(
            decide(LidAwakeInputs(enabled=False, supported=True, guard_ready=True,
                                  power=CHARGER, heat_level=0, agents_working=True)),
            LidAwakeReason.OFF,
        )

    def test_not_a_mac(self):
        self.assertEqual(
            decide(LidAwakeInputs(enabled=True, supported=False)),
            LidAwakeReason.UNSUPPORTED,
        )

    def test_no_guard_no_keep_awake_even_on_charger(self):
        reason = decide(LidAwakeInputs(
            enabled=True, supported=True, guard_ready=False,
            power=CHARGER, heat_level=0, agents_working=True,
        ))
        self.assertEqual(reason, LidAwakeReason.NEEDS_SETUP)
        self.assertNotIn(reason, KEEP_AWAKE_REASONS)

    def test_unreadable_charger_or_heat_lets_it_sleep(self):
        for power, heat in ((PowerState(), 0), (CHARGER, None)):
            inputs = LidAwakeInputs(**{**READY, 'heat_level': heat}, power=power)
            self.assertEqual(decide(inputs), LidAwakeReason.UNKNOWN_POWER)

    def test_too_hot_outranks_the_charger(self):
        inputs = LidAwakeInputs(**{**READY, 'heat_level': HEAT_LEVEL_TOO_HOT}, power=CHARGER)
        self.assertEqual(decide(inputs), LidAwakeReason.TOO_HOT)

    def test_one_below_too_hot_still_stays_awake(self):
        inputs = LidAwakeInputs(**{**READY, 'heat_level': HEAT_LEVEL_TOO_HOT - 1}, power=CHARGER)
        self.assertEqual(decide(inputs), LidAwakeReason.ON_CHARGER)

    def test_charger_stays_awake_with_nothing_running(self):
        reason = decide(LidAwakeInputs(**READY, power=CHARGER, agents_working=False))
        self.assertEqual(reason, LidAwakeReason.ON_CHARGER)
        self.assertIn(reason, KEEP_AWAKE_REASONS)

    def test_a_desktop_mac_has_no_battery_and_stays_awake(self):
        self.assertEqual(decide(LidAwakeInputs(**READY, power=DESKTOP_MAC)), LidAwakeReason.ON_CHARGER)

    def test_battery_with_unreadable_percent_lets_it_sleep(self):
        inputs = LidAwakeInputs(**READY, power=PowerState(on_charger=False))
        self.assertEqual(decide(inputs), LidAwakeReason.UNKNOWN_POWER)

    def test_battery_floor_is_inclusive(self):
        at_floor = PowerState(on_charger=False, battery_percent=BATTERY_FLOOR_PERCENT)
        above = PowerState(on_charger=False, battery_percent=BATTERY_FLOOR_PERCENT + 1)
        self.assertEqual(
            decide(LidAwakeInputs(**READY, power=at_floor, agents_working=True)),
            LidAwakeReason.LOW_BATTERY,
        )
        self.assertEqual(
            decide(LidAwakeInputs(**READY, power=above, agents_working=True)),
            LidAwakeReason.AGENT_WORKING,
        )

    def test_battery_with_no_agent_lets_it_sleep(self):
        reason = decide(LidAwakeInputs(**READY, power=BATTERY, agents_working=False))
        self.assertEqual(reason, LidAwakeReason.BATTERY_IDLE)
        self.assertNotIn(reason, KEEP_AWAKE_REASONS)


class NoHeatSensorTests(unittest.TestCase):
    NO_SENSOR = {**READY, 'heat_level': HEAT_LEVEL_NO_SENSOR}

    def test_on_the_charger_it_stays_awake(self):
        self.assertEqual(
            decide(LidAwakeInputs(**self.NO_SENSOR, power=CHARGER)),
            LidAwakeReason.ON_CHARGER,
        )

    def test_on_battery_it_never_does_even_with_an_agent_working(self):
        reason = decide(LidAwakeInputs(**self.NO_SENSOR, power=BATTERY, agents_working=True))
        self.assertEqual(reason, LidAwakeReason.NO_HEAT_SENSOR)
        self.assertNotIn(reason, KEEP_AWAKE_REASONS)

    def test_is_not_mistaken_for_a_failed_reading(self):
        self.assertNotEqual(
            decide(LidAwakeInputs(**self.NO_SENSOR, power=CHARGER)),
            LidAwakeReason.UNKNOWN_POWER,
        )

    def test_idle_sleep_is_blocked_on_the_charger_only(self):
        working = {**self.NO_SENSOR, 'agents_working': True}
        self.assertTrue(blocks_idle_sleep(LidAwakeInputs(**working, power=CHARGER)))
        self.assertFalse(blocks_idle_sleep(LidAwakeInputs(**working, power=BATTERY)))


class BlocksIdleSleepTests(unittest.TestCase):
    WORKING = {'enabled': True, 'supported': True, 'heat_level': 0, 'agents_working': True}

    def test_needs_no_guard(self):
        self.assertTrue(blocks_idle_sleep(LidAwakeInputs(**self.WORKING, power=BATTERY)))

    def test_on_the_charger(self):
        self.assertTrue(blocks_idle_sleep(LidAwakeInputs(**self.WORKING, power=DESKTOP_MAC)))

    def test_only_while_an_agent_works(self):
        inputs = LidAwakeInputs(**{**self.WORKING, 'agents_working': False}, power=CHARGER)
        self.assertFalse(blocks_idle_sleep(inputs))

    def test_never_when_off_or_not_a_mac(self):
        for change in ({'enabled': False}, {'supported': False}):
            inputs = LidAwakeInputs(**{**self.WORKING, **change}, power=CHARGER)
            self.assertFalse(blocks_idle_sleep(inputs))

    def test_same_heat_limit_and_unknown_heat_is_no(self):
        for heat in (None, HEAT_LEVEL_TOO_HOT):
            inputs = LidAwakeInputs(**{**self.WORKING, 'heat_level': heat}, power=CHARGER)
            self.assertFalse(blocks_idle_sleep(inputs))

    def test_same_battery_floor(self):
        at_floor = PowerState(on_charger=False, battery_percent=BATTERY_FLOOR_PERCENT)
        self.assertFalse(blocks_idle_sleep(LidAwakeInputs(**self.WORKING, power=at_floor)))

    def test_unknown_power_is_no(self):
        for power in (PowerState(), PowerState(on_charger=False)):
            self.assertFalse(blocks_idle_sleep(LidAwakeInputs(**self.WORKING, power=power)))


if __name__ == '__main__':
    unittest.main()
