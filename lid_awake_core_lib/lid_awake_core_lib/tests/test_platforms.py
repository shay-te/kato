from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

from lid_awake_core_lib.lid_awake_core_lib import (
    linux_power,
    mac_crash_guard,
    mac_power,
    platforms,
    windows_crash_guard,
    windows_power,
)
from lid_awake_core_lib.lid_awake_core_lib.idle_sleep_blocker import IdleSleepBlocker
from lid_awake_core_lib.lid_awake_core_lib.linux_inhibitor import (
    WHAT_IDLE_ONLY,
    WHAT_LID_CLOSED,
    LinuxInhibitor,
)
from lid_awake_core_lib.lid_awake_core_lib.mac_crash_guard import GuardIdentity
from lid_awake_core_lib.lid_awake_core_lib.mac_sleep_switch import MacSleepSwitch
from lid_awake_core_lib.lid_awake_core_lib.platform_support import UNSUPPORTED
from lid_awake_core_lib.lid_awake_core_lib.platforms import HostIdentity, platform_support
from lid_awake_core_lib.lid_awake_core_lib.windows_crash_guard import WindowsGuardIdentity
from lid_awake_core_lib.lid_awake_core_lib.windows_idle_blocker import (
    WindowsIdleSleepBlocker,
)
from lid_awake_core_lib.lid_awake_core_lib.windows_lid_switch import WindowsLidSwitch

HOST = HostIdentity(
    who='host',
    why='agents are working',
    heartbeat=Path('/tmp/beat'),
    mac=GuardIdentity(label='com.example.w', directory='/L/E', sudoers_name='example'),
    windows=WindowsGuardIdentity(
        run_value='Example', script=Path('/tmp/w.ps1'), saved=Path('/tmp/lid.json'),
    ),
)


class MacTests(unittest.TestCase):
    def setUp(self):
        self.support = platform_support(HOST, system='darwin')

    def test_uses_the_mac_parts(self):
        self.assertIsInstance(self.support.lid_switch, MacSleepSwitch)
        self.assertIsInstance(self.support.idle_blocker, IdleSleepBlocker)
        self.assertIs(self.support.read_power, mac_power.read_power_state)
        self.assertIs(self.support.read_heat, mac_power.read_heat_level)
        self.assertTrue(self.support.supported)

    def test_the_guard_uses_the_hosts_names(self):
        with mock.patch.object(mac_crash_guard, 'guard_is_ready', return_value=True) as ready, \
                mock.patch.object(mac_crash_guard, 'install_guard', return_value=0) as install, \
                mock.patch.object(mac_crash_guard, 'remove_guard', return_value=0) as remove:
            self.support.guard_ready()
            self.support.install_guard()
            self.support.remove_guard()
        ready.assert_called_once_with(HOST.mac)
        install.assert_called_once_with(HOST.mac, HOST.heartbeat)
        remove.assert_called_once_with(HOST.mac)


class WindowsTests(unittest.TestCase):
    def setUp(self):
        self.support = platform_support(HOST, system='win32')

    def test_uses_the_windows_parts(self):
        self.assertIsInstance(self.support.lid_switch, WindowsLidSwitch)
        self.assertIsInstance(self.support.idle_blocker, WindowsIdleSleepBlocker)
        self.assertIs(self.support.read_power, windows_power.read_power_state)
        self.assertIs(self.support.read_heat, windows_power.read_heat_level)

    def test_the_guard_uses_the_hosts_names(self):
        with mock.patch.object(windows_crash_guard, 'guard_is_ready', return_value=True) as ready, \
                mock.patch.object(windows_crash_guard, 'install_guard', return_value=0) as install, \
                mock.patch.object(windows_crash_guard, 'remove_guard', return_value=0) as remove:
            self.support.guard_ready()
            self.support.install_guard()
            self.support.remove_guard()
        ready.assert_called_once_with(HOST.windows)
        install.assert_called_once_with(HOST.windows, HOST.heartbeat)
        remove.assert_called_once_with(HOST.windows, self.support.lid_switch)

    def test_the_switch_starts_the_hosts_watchdog(self):
        with mock.patch.object(windows_crash_guard, 'start_watchdog') as start:
            self.support.lid_switch._start_watchdog()
        start.assert_called_once_with(HOST.windows.script)


class LinuxTests(unittest.TestCase):
    def _support(self, *, inhibit='/usr/bin/systemd-inhibit', logind=True, release='6.8.0-generic'):
        with mock.patch.object(platforms.shutil, 'which', return_value=inhibit), \
                mock.patch.object(platforms, 'LOGIND_RUNTIME') as runtime:
            runtime.is_dir.return_value = logind
            return platform_support(HOST, system='linux', release=release)

    def test_uses_the_linux_parts(self):
        support = self._support()
        self.assertIsInstance(support.lid_switch, LinuxInhibitor)
        self.assertIsInstance(support.idle_blocker, LinuxInhibitor)
        self.assertIs(support.read_power, linux_power.read_power_state)
        self.assertIs(support.read_heat, linux_power.read_heat_level)

    def test_the_two_holds_block_different_things(self):
        support = self._support()
        self.assertIn(f'--what={WHAT_LID_CLOSED}', support.lid_switch.command())
        self.assertIn(f'--what={WHAT_IDLE_ONLY}', support.idle_blocker.command())

    def test_needs_no_setup(self):
        support = self._support()
        self.assertTrue(support.guard_ready())
        self.assertEqual(support.install_guard(), 0)
        self.assertEqual(support.remove_guard(), 0)

    def test_wsl_is_not_supported_because_windows_owns_the_lid(self):
        self.assertIs(self._support(release='5.15.90.1-microsoft-standard-WSL2'), UNSUPPORTED)

    def test_no_systemd_is_not_supported(self):
        self.assertIs(self._support(inhibit=None), UNSUPPORTED)
        self.assertIs(self._support(logind=False), UNSUPPORTED)

    def test_reads_the_real_kernel_release_by_default(self):
        with mock.patch.object(platforms.platform, 'uname') as uname:
            uname.return_value.release = 'x-microsoft-y'
            self.assertIs(platform_support(HOST, system='linux'), UNSUPPORTED)


class OtherTests(unittest.TestCase):
    def test_anything_else_is_unsupported(self):
        self.assertIs(platform_support(HOST, system='freebsd14'), UNSUPPORTED)

    def test_defaults_to_this_computer(self):
        with mock.patch.object(platforms.sys, 'platform', 'freebsd14'):
            self.assertIs(platform_support(HOST), UNSUPPORTED)


class UnsupportedDefaultsTests(unittest.TestCase):
    def test_does_nothing_and_says_so(self):
        self.assertFalse(UNSUPPORTED.supported)
        self.assertFalse(UNSUPPORTED.guard_ready())
        self.assertIsNone(UNSUPPORTED.read_heat())
        self.assertIsNone(UNSUPPORTED.read_power().on_charger)
        self.assertEqual(UNSUPPORTED.install_guard(), 0)


@unittest.skipUnless(sys.platform == 'darwin', 'this computer')
class ThisMacTests(unittest.TestCase):
    def test_a_mac_gets_the_mac_parts(self):
        self.assertIsInstance(platform_support(HOST).lid_switch, MacSleepSwitch)


if __name__ == '__main__':
    unittest.main()
