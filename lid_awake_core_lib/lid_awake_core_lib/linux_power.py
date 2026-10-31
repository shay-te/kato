"""Read Linux's charger, battery and heat from ``/sys``. None of it needs root."""

from __future__ import annotations

from pathlib import Path

from lid_awake_core_lib.lid_awake_core_lib.decision import (
    HEAT_LEVEL_NO_SENSOR,
    PowerState,
)

POWER_SUPPLY_ROOT = Path('/sys/class/power_supply')
THERMAL_ROOT = Path('/sys/class/thermal')

# Maps degrees onto the Mac's levels. Laptops throttle in the 90s.
_HEAT_LEVELS_CELSIUS = ((100, 3), (90, 2), (80, 1))


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding='utf-8').strip()
    except OSError:
        return ''


def read_power_state(root: Path = POWER_SUPPLY_ROOT) -> PowerState:
    batteries: list[Path] = []
    mains_online = False
    for supply in sorted(root.glob('*')):
        if _read(supply / 'type') == 'Battery':
            batteries.append(supply)
        elif _read(supply / 'online') == '1':
            mains_online = True
    if not batteries:
        return PowerState(on_charger=True)
    battery = batteries[0]
    capacity = _read(battery / 'capacity')
    return PowerState(
        on_charger=mains_online or _read(battery / 'status') != 'Discharging',
        battery_percent=int(capacity) if capacity.isdigit() else None,
    )


def heat_level_for(celsius: float) -> int:
    for threshold, level in _HEAT_LEVELS_CELSIUS:
        if celsius >= threshold:
            return level
    return 0


def read_heat_level(root: Path = THERMAL_ROOT) -> int:
    temperatures = [
        int(text)
        for text in (_read(zone / 'temp') for zone in root.glob('thermal_zone*'))
        if text.isdigit()
    ]
    if not temperatures:
        return HEAT_LEVEL_NO_SENSOR
    return heat_level_for(max(temperatures) / 1000)
