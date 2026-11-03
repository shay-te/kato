"""Ticket states that mean done, beside the task provider's own done state.

A workflow often has more than one: "Waiting Deploy" is merged work waiting to
ship, and to whoever waits on it as finished as "Done". The tracker cannot say
so (YouTrack does not mark such a state resolved), so the operator lists them.
Read fresh on every use, so a save in Settings needs no restart.
"""

from __future__ import annotations

from kato_core_lib.helpers.kato_settings_store_utils import live_setting

DONE_STATES_KEY = 'KATO_DONE_STATES'


def extra_done_states() -> frozenset[str]:
    """The listed states, lower-cased (comma-separated in the setting)."""
    return frozenset(
        state.strip().lower() for state in live_setting(DONE_STATES_KEY).split(',') if state.strip()
    )


def is_done_state(state: str, done_state: str) -> bool:
    """Whether a ticket in ``state`` is done: the provider's done state, or a listed one."""
    normalized = str(state or '').strip().lower()
    if not normalized:
        return False
    return normalized == str(done_state or '').strip().lower() or normalized in extra_done_states()
