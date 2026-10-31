"""The whole story, the way a host lives it: turn on, close the lid, crash.

Real heartbeat file, the real watchdog script in a real shell, and a fake
``pmset`` holding the flag, so the service and the watchdog really do share one
Mac. Only the Mac itself is simulated.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from lid_awake_core_lib.lid_awake_core_lib.decision import LidAwakeReason, PowerState
from lid_awake_core_lib.lid_awake_core_lib.heartbeat import Heartbeat
from lid_awake_core_lib.lid_awake_core_lib.mac_crash_guard import (
    STALE_AFTER_MINUTES,
    render_watchdog,
)
from lid_awake_core_lib.lid_awake_core_lib.platform_support import PlatformSupport
from lid_awake_core_lib.lid_awake_core_lib.service import LidAwakeService


class FakeMac:
    """One sleep flag, shared by the host's switch and the watchdog's ``pmset``."""

    def __init__(self, root: Path) -> None:
        self.flag = root / 'flag'
        self.flag.write_text('0')
        self.pmset = root / 'pmset'
        self.pmset.write_text(
            '#!/bin/sh\n'
            'if [ "$1" = "-g" ]; then\n'
            f'  [ "$(cat "{self.flag}")" = 1 ] && echo " SleepDisabled  1"\n'
            '  exit 0\n'
            'fi\n'
            f'echo "$3" > "{self.flag}"\n'
        )
        self.pmset.chmod(self.pmset.stat().st_mode | stat.S_IEXEC)

    def is_held(self) -> bool:
        return self.flag.read_text().strip() == '1'

    def set_held(self, held: bool) -> str:
        self.flag.write_text('1' if held else '0')
        return ''


class NoCaffeinate:
    holding = False

    def hold(self) -> None: ...

    def release(self) -> None: ...


@unittest.skipIf(sys.platform == 'win32', 'runs the Mac watchdog in a POSIX shell')
class LidClosedFlowTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.mac = FakeMac(self.root)
        self.heartbeat_file = self.root / 'heartbeat'
        self.watchdog = self.root / 'watchdog.sh'
        self.watchdog.write_text(render_watchdog(self.heartbeat_file, pmset=str(self.mac.pmset)))
        self.enabled = True
        self.power = PowerState(on_charger=True, battery_percent=90)
        self.working = False
        self.service = self._start_host()

    def _start_host(self) -> LidAwakeService:
        return LidAwakeService(
            is_enabled=lambda: self.enabled,
            agents_working=lambda: self.working,
            heartbeat=Heartbeat(self.heartbeat_file),
            support=PlatformSupport(
                lid_switch=self.mac,
                idle_blocker=NoCaffeinate(),
                read_power=lambda: self.power,
                read_heat=lambda: 0,
                guard_ready=lambda: True,
            ),
        )

    def _watchdog_runs(self) -> None:
        subprocess.run(['/bin/sh', str(self.watchdog)], check=True, capture_output=True)

    def _time_passes_with_no_heartbeat(self) -> None:
        when = time.time() - (STALE_AFTER_MINUTES + 2) * 60
        os.utime(self.heartbeat_file, (when, when))

    def test_a_healthy_host_is_never_interrupted(self):
        self.service.tick()
        self._watchdog_runs()
        self.assertTrue(self.mac.is_held())

    def test_a_crash_is_cleaned_up_by_the_watchdog_alone(self):
        self.service.tick()
        self.assertTrue(self.mac.is_held())
        # The host dies here: no stop(), no more ticks.
        self._time_passes_with_no_heartbeat()
        self._watchdog_runs()
        self.assertFalse(self.mac.is_held())

    def test_the_next_run_recovers_from_that_crash(self):
        self.service.tick()
        self._time_passes_with_no_heartbeat()
        self._watchdog_runs()
        self.service = self._start_host()
        self.service.tick()
        self.assertTrue(self.mac.is_held())
        self._watchdog_runs()
        self.assertTrue(self.mac.is_held())

    def test_a_crash_with_the_setting_since_turned_off_is_cleaned_up_at_the_next_start(self):
        self.service.tick()
        self.enabled = False
        self.service = self._start_host()
        self.service.tick()
        self.assertFalse(self.mac.is_held())
        self.assertFalse(self.heartbeat_file.exists())

    def test_a_clean_exit_leaves_nothing_for_the_watchdog(self):
        self.service.tick()
        self.service.stop()
        self.assertFalse(self.mac.is_held())
        self.assertFalse(self.heartbeat_file.exists())

    def test_the_operators_own_pmset_survives_the_watchdog(self):
        self.mac.set_held(True)
        self._watchdog_runs()
        self.assertTrue(self.mac.is_held())

    def test_unplugged_mid_run_then_the_agent_finishes(self):
        self.working = True
        self.service.tick()
        self.assertEqual(self.service.status()['reason'], LidAwakeReason.ON_CHARGER)
        self.power = PowerState(on_charger=False, battery_percent=60)
        self.service.tick()
        self.assertEqual(self.service.status()['reason'], LidAwakeReason.AGENT_WORKING)
        self.assertTrue(self.mac.is_held())
        self.working = False
        self.service.tick()
        self.assertEqual(self.service.status()['reason'], LidAwakeReason.BATTERY_IDLE)
        self.assertFalse(self.mac.is_held())


if __name__ == '__main__':
    unittest.main()
