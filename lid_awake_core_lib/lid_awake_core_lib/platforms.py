"""Pick what this computer supports. Everything platform-specific is decided here."""

from __future__ import annotations

import platform
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from lid_awake_core_lib.lid_awake_core_lib import (
    linux_inhibitor,
    linux_power,
    mac_crash_guard,
    mac_power,
    windows_crash_guard,
    windows_power,
)
from lid_awake_core_lib.lid_awake_core_lib.idle_sleep_blocker import IdleSleepBlocker
from lid_awake_core_lib.lid_awake_core_lib.linux_inhibitor import LinuxInhibitor
from lid_awake_core_lib.lid_awake_core_lib.mac_crash_guard import GuardIdentity
from lid_awake_core_lib.lid_awake_core_lib.mac_sleep_switch import MacSleepSwitch
from lid_awake_core_lib.lid_awake_core_lib.platform_support import (
    UNSUPPORTED,
    PlatformSupport,
)
from lid_awake_core_lib.lid_awake_core_lib.windows_crash_guard import WindowsGuardIdentity
from lid_awake_core_lib.lid_awake_core_lib.windows_idle_blocker import (
    WindowsIdleSleepBlocker,
)
from lid_awake_core_lib.lid_awake_core_lib.windows_lid_switch import WindowsLidSwitch

LOGIND_RUNTIME = Path('/run/systemd/system')


@dataclass(frozen=True)
class HostIdentity:
    """The host's names, for the system places each platform writes to."""

    who: str
    why: str
    heartbeat: Path
    mac: GuardIdentity
    windows: WindowsGuardIdentity


def _for_mac(host: HostIdentity) -> PlatformSupport:
    return PlatformSupport(
        lid_switch=MacSleepSwitch(),
        idle_blocker=IdleSleepBlocker(),
        read_power=mac_power.read_power_state,
        read_heat=mac_power.read_heat_level,
        guard_ready=lambda: mac_crash_guard.guard_is_ready(host.mac),
        install_guard=lambda: mac_crash_guard.install_guard(host.mac, host.heartbeat),
        remove_guard=lambda: mac_crash_guard.remove_guard(host.mac),
    )


def _for_windows(host: HostIdentity) -> PlatformSupport:
    switch = WindowsLidSwitch(
        saved=host.windows.saved,
        start_watchdog=lambda: windows_crash_guard.start_watchdog(host.windows.script),
    )
    return PlatformSupport(
        lid_switch=switch,
        idle_blocker=WindowsIdleSleepBlocker(),
        read_power=windows_power.read_power_state,
        read_heat=windows_power.read_heat_level,
        guard_ready=lambda: windows_crash_guard.guard_is_ready(host.windows),
        install_guard=lambda: windows_crash_guard.install_guard(host.windows, host.heartbeat),
        remove_guard=lambda: windows_crash_guard.remove_guard(host.windows, switch),
    )


def _for_linux(host: HostIdentity) -> PlatformSupport:
    return PlatformSupport(
        lid_switch=LinuxInhibitor(
            what=linux_inhibitor.WHAT_LID_CLOSED,
            who=host.who,
            why=host.why,
            heartbeat=host.heartbeat,
        ),
        idle_blocker=LinuxInhibitor(
            what=linux_inhibitor.WHAT_IDLE_ONLY,
            who=host.who,
            why=host.why,
        ),
        read_power=linux_power.read_power_state,
        read_heat=linux_power.read_heat_level,
        guard_ready=lambda: True,
    )


def _is_wsl(release: str) -> bool:
    # Windows owns the lid there, and logind inside the VM never hears of it.
    return 'microsoft' in release.lower()


def _has_logind() -> bool:
    return bool(shutil.which(linux_inhibitor.SYSTEMD_INHIBIT)) and LOGIND_RUNTIME.is_dir()


def platform_support(
    host: HostIdentity,
    *,
    system: str | None = None,
    release: str | None = None,
) -> PlatformSupport:
    system = sys.platform if system is None else system
    if system == 'darwin':
        return _for_mac(host)
    if system == 'win32':
        return _for_windows(host)
    if system.startswith('linux'):
        release = platform.uname().release if release is None else release
        if not _is_wsl(release) and _has_logind():
            return _for_linux(host)
    return UNSUPPORTED
