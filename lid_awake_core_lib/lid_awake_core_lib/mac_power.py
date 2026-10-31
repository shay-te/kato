"""Read the Mac's charger, battery and heat. None of this needs root."""

from __future__ import annotations

import re

from lid_awake_core_lib.lid_awake_core_lib.decision import PowerState
from lid_awake_core_lib.lid_awake_core_lib.commands import run_command

PMSET = '/usr/bin/pmset'
OSASCRIPT = '/usr/bin/osascript'

_DRAWING_FROM = re.compile(r"drawing from '([^']+)'")
_BATTERY_PERCENT = re.compile(r'(\d+)%')

# ``NSProcessInfo.thermalState``, reached through JavaScript for Automation
# because it is the one heat reading macOS gives a normal user: every
# temperature sensor needs root. It is a level, not degrees.
_HEAT_LEVEL_SCRIPT = (
    'ObjC.import("Foundation"); $.NSProcessInfo.processInfo.thermalState'
)


def parse_power_state(output: str) -> PowerState:
    """``pmset -g batt``: 'Now drawing from 'AC Power'' plus a percent, if any."""
    source = _DRAWING_FROM.search(output)
    if source is None:
        return PowerState()
    percent = _BATTERY_PERCENT.search(output)
    return PowerState(
        on_charger=source.group(1) == 'AC Power',
        battery_percent=int(percent.group(1)) if percent else None,
    )


def read_power_state() -> PowerState:
    finished = run_command([PMSET, '-g', 'batt'])
    if finished is None or finished.returncode != 0:
        return PowerState()
    return parse_power_state(finished.stdout)


def read_heat_level() -> int | None:
    finished = run_command([OSASCRIPT, '-l', 'JavaScript', '-e', _HEAT_LEVEL_SCRIPT])
    if finished is None or finished.returncode != 0:
        return None
    text = finished.stdout.strip()
    return int(text) if text.isdigit() else None
