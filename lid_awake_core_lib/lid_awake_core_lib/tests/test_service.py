from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lid_awake_core_lib.lid_awake_core_lib.decision import LidAwakeReason, PowerState
from lid_awake_core_lib.lid_awake_core_lib.heartbeat import Heartbeat
from lid_awake_core_lib.lid_awake_core_lib.platform_support import (
    UNSUPPORTED,
    PlatformSupport,
)
from lid_awake_core_lib.lid_awake_core_lib.service import (
    GUARD_RECHECK_SECONDS,
    LidAwakeService,
)

CHARGER = PowerState(on_charger=True, battery_percent=100)
BATTERY = PowerState(on_charger=False, battery_percent=80)


class FakeSwitch:
    def __init__(self, *, disabled=False, error=''):
        self.disabled = disabled
        self.error = error
        self.heartbeat_when_set = None
        self.heartbeat = None
        self.changes = []

    def is_held(self):
        return self.disabled

    def set_held(self, disabled):
        self.changes.append(disabled)
        self.heartbeat_when_set = self.heartbeat.exists()
        if self.error:
            return self.error
        self.disabled = disabled
        return ''


class FakeBlocker:
    def __init__(self):
        self.holding = False

    def hold(self):
        self.holding = True

    def release(self):
        self.holding = False


class ServiceTestCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.heartbeat = Heartbeat(Path(folder.name) / 'heartbeat')
        self.switch = FakeSwitch()
        self.switch.heartbeat = self.heartbeat
        self.blocker = FakeBlocker()
        self.enabled = True
        self.working = False
        self.guard = True
        self.guard_calls = 0
        self.power = CHARGER
        self.heat = 0
        self.now = 0.0
        self.logger = mock.Mock()
        self.power_reads = 0
        self.service = self._service()

    def _read_power(self):
        self.power_reads += 1
        return self.power

    def _service(self, support=None):
        return LidAwakeService(
            is_enabled=lambda: self.enabled,
            agents_working=lambda: self.working,
            heartbeat=self.heartbeat,
            support=support or PlatformSupport(
                lid_switch=self.switch,
                idle_blocker=self.blocker,
                read_power=self._read_power,
                read_heat=lambda: self.heat,
                guard_ready=self._guard_ready,
            ),
            clock=lambda: self.now,
            logger=self.logger,
        )

    def _guard_ready(self):
        self.guard_calls += 1
        return self.guard


class HoldTests(ServiceTestCase):
    def test_on_the_charger_it_holds_and_beats(self):
        self.service.tick()
        self.assertIs(self.switch.disabled, True)
        self.assertTrue(self.heartbeat.exists())
        self.assertEqual(self.service.status(), {
            'reason': LidAwakeReason.ON_CHARGER, 'active': True, 'error': '',
        })

    def test_the_heartbeat_exists_before_the_switch_changes(self):
        self.service.tick()
        self.assertIs(self.switch.heartbeat_when_set, True)

    def test_later_ticks_only_beat(self):
        self.service.tick()
        self.service.tick()
        self.assertEqual(self.switch.changes, [True])

    def test_someone_else_turning_it_off_is_noticed(self):
        self.service.tick()
        self.switch.disabled = False
        self.service.tick()
        self.assertEqual(self.switch.changes, [True, True])

    def test_a_refused_hold_is_reported_not_active(self):
        self.switch.error = 'pmset failed: no'
        self.service.tick()
        self.assertEqual(self.service.status(), {
            'reason': LidAwakeReason.ON_CHARGER, 'active': False, 'error': 'pmset failed: no',
        })

    def test_no_guard_never_touches_the_switch(self):
        self.guard = False
        self.service.tick()
        self.assertEqual(self.switch.changes, [])
        self.assertFalse(self.heartbeat.exists())
        self.assertEqual(self.service.status()['reason'], LidAwakeReason.NEEDS_SETUP)

    def test_battery_holds_only_while_an_agent_works(self):
        self.power = BATTERY
        self.working = True
        self.service.tick()
        self.assertIs(self.switch.disabled, True)
        self.working = False
        self.service.tick()
        self.assertIs(self.switch.disabled, False)
        self.assertFalse(self.heartbeat.exists())

    def test_too_hot_releases(self):
        self.service.tick()
        self.heat = 2
        self.service.tick()
        self.assertIs(self.switch.disabled, False)
        self.assertEqual(self.service.status()['reason'], LidAwakeReason.TOO_HOT)

    def test_a_failure_reading_the_agents_counts_as_not_working(self):
        self.power = BATTERY
        self.service._agents_working = mock.Mock(side_effect=RuntimeError('boom'))
        self.service.tick()
        self.assertEqual(self.service.status()['reason'], LidAwakeReason.BATTERY_IDLE)
        self.logger.exception.assert_called_once()


class ReleaseTests(ServiceTestCase):
    def test_turning_the_setting_off_restores_sleep(self):
        self.service.tick()
        self.enabled = False
        self.service.tick()
        self.assertIs(self.switch.disabled, False)
        self.assertFalse(self.heartbeat.exists())
        self.assertEqual(self.service.status()['reason'], LidAwakeReason.OFF)

    def test_a_crashed_runs_leftovers_are_cleaned_up_with_the_setting_off(self):
        self.heartbeat.beat()
        self.switch.disabled = True
        self.enabled = False
        self.service.tick()
        self.assertIs(self.switch.disabled, False)
        self.assertFalse(self.heartbeat.exists())

    def test_the_operators_own_pmset_is_left_alone(self):
        self.switch.disabled = True
        self.enabled = False
        self.service.tick()
        self.assertEqual(self.switch.changes, [])

    def test_an_operators_pmset_is_left_alone_on_battery_too(self):
        self.switch.disabled = True
        self.power = BATTERY
        self.service.tick()
        self.assertEqual(self.switch.changes, [])

    def test_a_failed_release_keeps_the_heartbeat_for_the_watchdog(self):
        self.service.tick()
        self.enabled = False
        self.switch.error = 'pmset failed: rule gone'
        self.service.tick()
        self.assertTrue(self.heartbeat.exists())
        self.assertEqual(self.service.status()['error'], 'pmset failed: rule gone')
        self.assertFalse(self.service.status()['active'])

    def test_already_restored_needs_no_command(self):
        self.heartbeat.beat()
        self.enabled = False
        self.service.tick()
        self.assertEqual(self.switch.changes, [])
        self.assertFalse(self.heartbeat.exists())

    def test_an_unreadable_switch_is_treated_as_held(self):
        self.heartbeat.beat()
        self.enabled = False
        self.switch.is_held = lambda: None
        self.service.tick()
        self.assertEqual(self.switch.changes, [False])

    def test_stop_restores_sleep(self):
        self.service.tick()
        self.service.stop()
        self.assertIs(self.switch.disabled, False)
        self.assertFalse(self.heartbeat.exists())

    def test_stop_says_when_it_could_not(self):
        self.service.tick()
        self.switch.error = 'pmset failed: no'
        self.service.stop()
        self.logger.warning.assert_called_once()

    def test_stop_with_nothing_held_does_nothing(self):
        self.service.stop()
        self.assertEqual(self.switch.changes, [])


class IdleBlockerTests(ServiceTestCase):
    def test_holds_while_an_agent_works_with_no_setup(self):
        self.guard = False
        self.working = True
        self.service.tick()
        self.assertTrue(self.blocker.holding)
        self.assertEqual(self.switch.changes, [])

    def test_lets_go_when_the_agent_stops_on_battery(self):
        self.power = BATTERY
        self.working = True
        self.service.tick()
        self.working = False
        self.service.tick()
        self.assertFalse(self.blocker.holding)

    def test_turning_the_setting_off_mid_run_lets_go(self):
        self.working = True
        self.service.tick()
        self.enabled = False
        self.service.tick()
        self.assertFalse(self.blocker.holding)

    def test_stop_lets_go(self):
        self.working = True
        self.service.tick()
        self.service.stop()
        self.assertFalse(self.blocker.holding)

    def test_held_with_no_agent_too_because_windows_idle_timer_still_runs(self):
        self.working = False
        self.service.tick()
        self.assertTrue(self.blocker.holding)


class SupportTests(ServiceTestCase):
    def test_off_reads_nothing_from_the_system(self):
        self.enabled = False
        self.service.tick()
        self.assertEqual(self.power_reads, 0)

    def test_off_after_a_crash_does_not_read_the_system_either(self):
        self.enabled = False
        self.heartbeat.beat()
        self.service.tick()
        self.assertEqual(self.power_reads, 0)

    def test_an_unsupported_system_is_said_so_and_nothing_is_touched(self):
        service = self._service(support=UNSUPPORTED)
        service.tick()
        self.assertEqual(self.power_reads, 0)
        self.assertEqual(self.switch.changes, [])
        self.assertEqual(service.status()['reason'], LidAwakeReason.UNSUPPORTED)

    def test_a_stray_heartbeat_on_an_unsupported_system_is_not_a_crash(self):
        self.heartbeat.beat()
        service = self._service(support=UNSUPPORTED)
        self.enabled = False
        service.tick()
        service.stop()
        self.assertEqual(service.status()['reason'], LidAwakeReason.OFF)


class GuardCacheTests(ServiceTestCase):
    def test_a_good_answer_is_reused(self):
        self.service.tick()
        self.service.tick()
        self.assertEqual(self.guard_calls, 1)

    def test_and_rechecked_later(self):
        self.service.tick()
        self.now += GUARD_RECHECK_SECONDS + 1
        self.service.tick()
        self.assertEqual(self.guard_calls, 2)

    def test_a_bad_answer_is_rechecked_every_tick(self):
        self.guard = False
        self.service.tick()
        self.service.tick()
        self.assertEqual(self.guard_calls, 2)


class LoggingTests(ServiceTestCase):
    def test_logs_changes_only(self):
        self.service.tick()
        self.service.tick()
        self.service.tick()
        self.assertEqual(self.logger.info.call_count, 1)


class LifecycleTests(ServiceTestCase):
    def test_start_ticks_on_a_thread(self):
        self.service._ticker = mock.Mock()
        self.service.start()
        self.service._ticker.start.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
