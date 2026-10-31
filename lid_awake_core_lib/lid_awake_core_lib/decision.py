"""The rules for keeping a computer awake with the lid closed. No I/O.

Everything unknown decides the same way: let it sleep. A sleeping computer only
pauses the host; one wrongly held awake can overheat in a bag.
"""

from __future__ import annotations

from dataclasses import dataclass

# Below this on battery the computer sleeps, so it never runs itself flat.
BATTERY_FLOOR_PERCENT = 20

# Every platform reports heat on the Mac's scale, from
# ``NSProcessInfo.thermalState``: 0 nominal, 1 fair, 2 serious, 3 critical.
HEAT_LEVEL_TOO_HOT = 2
# This computer has no heat reading a normal user can get. Not the same as
# ``None``, a reading that failed: with no sensor at all the fans and firmware
# are the only protection, which is enough on a charger and not on a battery.
HEAT_LEVEL_NO_SENSOR = -1


class LidAwakeReason:
    OFF = 'off'
    UNSUPPORTED = 'unsupported'
    NEEDS_SETUP = 'needs_setup'
    UNKNOWN_POWER = 'unknown_power'
    TOO_HOT = 'too_hot'
    ON_CHARGER = 'on_charger'
    LOW_BATTERY = 'low_battery'
    NO_HEAT_SENSOR = 'no_heat_sensor'
    AGENT_WORKING = 'agent_working'
    BATTERY_IDLE = 'battery_idle'


KEEP_AWAKE_REASONS = frozenset({
    LidAwakeReason.ON_CHARGER,
    LidAwakeReason.AGENT_WORKING,
})


@dataclass(frozen=True)
class PowerState:
    """``on_charger`` / ``battery_percent``: ``None`` when it could not be read
    (a desktop Mac has no battery, so its percent stays ``None`` on charger)."""

    on_charger: bool | None = None
    battery_percent: int | None = None


@dataclass(frozen=True)
class LidAwakeInputs:
    enabled: bool = False
    supported: bool = False
    guard_ready: bool = False
    power: PowerState = PowerState()
    heat_level: int | None = None
    agents_working: bool = False


def blocks_idle_sleep(inputs: LidAwakeInputs) -> bool:
    """Should the computer stop idling to sleep? Only while an agent is working.

    This is the milder of the two holds: it needs no root and no guard, because
    it ends with the host, and it does nothing about a closed lid. The battery
    and heat limits are the same, so the two holds never disagree about them.
    """
    if not (inputs.enabled and inputs.supported and inputs.agents_working):
        return False
    if inputs.heat_level is None or inputs.heat_level >= HEAT_LEVEL_TOO_HOT:
        return False
    if inputs.power.on_charger:
        return True
    percent = inputs.power.battery_percent
    return (
        inputs.power.on_charger is False
        and inputs.heat_level != HEAT_LEVEL_NO_SENSOR
        and percent is not None
        and percent > BATTERY_FLOOR_PERCENT
    )


def decide(inputs: LidAwakeInputs) -> str:
    """The ``LidAwakeReason`` that applies; awake only for ``KEEP_AWAKE_REASONS``."""
    if not inputs.enabled:
        return LidAwakeReason.OFF
    if not inputs.supported:
        return LidAwakeReason.UNSUPPORTED
    # No watchdog, no keep-awake: without it a host crash would leave the
    # computer unable to sleep at all.
    if not inputs.guard_ready:
        return LidAwakeReason.NEEDS_SETUP
    if inputs.power.on_charger is None or inputs.heat_level is None:
        return LidAwakeReason.UNKNOWN_POWER
    if inputs.heat_level >= HEAT_LEVEL_TOO_HOT:
        return LidAwakeReason.TOO_HOT
    if inputs.power.on_charger:
        # Awake the whole time, not only during a run: it has to be up
        # for the next scan to pick up a new ticket.
        return LidAwakeReason.ON_CHARGER
    if inputs.power.battery_percent is None:
        return LidAwakeReason.UNKNOWN_POWER
    if inputs.power.battery_percent <= BATTERY_FLOOR_PERCENT:
        return LidAwakeReason.LOW_BATTERY
    if inputs.heat_level == HEAT_LEVEL_NO_SENSOR:
        return LidAwakeReason.NO_HEAT_SENSOR
    if inputs.agents_working:
        return LidAwakeReason.AGENT_WORKING
    return LidAwakeReason.BATTERY_IDLE
