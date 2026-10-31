"""What the UI says about the lid. The page shows these words and decides nothing."""

from __future__ import annotations

from lid_awake_core_lib.lid_awake_core_lib.decision import (
    BATTERY_FLOOR_PERCENT,
    LidAwakeReason,
)

LEVEL_ON = 'on'
LEVEL_INFO = 'info'
LEVEL_WARN = 'warn'
LEVEL_ERROR = 'error'

_TEXT = {
    LidAwakeReason.UNSUPPORTED: (
        LEVEL_WARN, 'Lid closed: not supported',
        'Keeping kato running with the lid closed does not work on this computer '
        'yet. Closing the lid will stop kato.',
    ),
    LidAwakeReason.NEEDS_SETUP: (
        LEVEL_WARN, 'Lid closed: setup needed',
        'Run `kato lid-awake setup` once in a terminal. Until then, closing the '
        'lid sleeps this computer and kato stops. With the lid open, it stays '
        'awake while an agent is working.',
    ),
    LidAwakeReason.UNKNOWN_POWER: (
        LEVEL_WARN, 'Lid closed: will sleep',
        'Kato could not read the battery or heat level, so it is letting this '
        'computer sleep. Closing the lid will stop kato.',
    ),
    LidAwakeReason.TOO_HOT: (
        LEVEL_WARN, 'Lid closed: too hot',
        'This computer is running hot, so kato is letting it sleep. Closing the '
        'lid will stop kato until it cools down.',
    ),
    LidAwakeReason.ON_CHARGER: (
        LEVEL_ON, 'Lid can close',
        'On the charger: kato keeps running with the lid closed, and this '
        'computer will not sleep. Keep it on a hard surface, never in a bag.',
    ),
    LidAwakeReason.LOW_BATTERY: (
        LEVEL_WARN, 'Lid closed: low battery',
        f'Battery is at {BATTERY_FLOOR_PERCENT}% or below, so kato is letting '
        'this computer sleep. Closing the lid will stop kato.',
    ),
    LidAwakeReason.NO_HEAT_SENSOR: (
        LEVEL_INFO, 'Lid closed: will sleep',
        'On battery: this computer cannot report its heat, so kato lets it sleep '
        'with the lid closed. Plug in the charger to keep kato running.',
    ),
    LidAwakeReason.AGENT_WORKING: (
        LEVEL_ON, 'Lid can close',
        'On battery: this computer stays awake with the lid closed while an '
        f'agent is working, down to {BATTERY_FLOOR_PERCENT}% battery. Keep it on '
        'a hard surface, never in a bag.',
    ),
    LidAwakeReason.BATTERY_IDLE: (
        LEVEL_INFO, 'Lid closed: will sleep',
        'On battery with no agent working: closing the lid sleeps this computer, '
        'and kato will not pick up new tickets until you open it.',
    ),
}


def status_payload(status: dict) -> dict:
    """The lib's ``{reason, active, error}``, with the words the UI shows.

    ``label`` stays empty while there is nothing to say.
    """
    reason = status.get('reason', LidAwakeReason.OFF)
    error = status.get('error', '')
    level, label, detail = _TEXT.get(reason, ('', '', ''))
    if error:
        level = LEVEL_ERROR
        label = 'Lid closed: failed'
        detail = (
            f'Kato could not change this computer\'s sleep setting: {error}. '
            'Closing the lid may stop kato.'
        )
    return {
        'reason': reason,
        'active': bool(status.get('active')),
        'level': level,
        'label': label,
        'detail': detail,
    }
