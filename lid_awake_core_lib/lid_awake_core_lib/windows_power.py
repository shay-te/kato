"""Read Windows' charger and battery. Neither needs elevation.

Heat has no answer here: the ACPI thermal zone needs elevation and many laptops
do not fill it in, and the performance counter has a localized name. So Windows
reports "no sensor" rather than a number that is sometimes invented.
"""

from __future__ import annotations

import ctypes

from lid_awake_core_lib.lid_awake_core_lib.decision import (
    HEAT_LEVEL_NO_SENSOR,
    PowerState,
)

_AC_ONLINE = 1
_AC_OFFLINE = 0
_UNKNOWN_PERCENT = 255


class SystemPowerStatus(ctypes.Structure):
    _fields_ = [
        ('ACLineStatus', ctypes.c_ubyte),
        ('BatteryFlag', ctypes.c_ubyte),
        ('BatteryLifePercent', ctypes.c_ubyte),
        ('SystemStatusFlag', ctypes.c_ubyte),
        ('BatteryLifeTime', ctypes.c_uint32),
        ('BatteryFullLifeTime', ctypes.c_uint32),
    ]


def power_state_from(ac_line: int, percent: int) -> PowerState:
    on_charger = {_AC_ONLINE: True, _AC_OFFLINE: False}.get(ac_line)
    return PowerState(
        on_charger=on_charger,
        battery_percent=None if percent == _UNKNOWN_PERCENT else percent,
    )


def read_power_state(kernel32=None) -> PowerState:
    kernel32 = kernel32 or ctypes.windll.kernel32
    status = SystemPowerStatus()
    if not kernel32.GetSystemPowerStatus(ctypes.byref(status)):
        return PowerState()
    return power_state_from(status.ACLineStatus, status.BatteryLifePercent)


def read_heat_level() -> int:
    return HEAT_LEVEL_NO_SENSOR
