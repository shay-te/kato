"""Ticket states that mean done, beside the task provider's own done state.

A workflow often has more than one: "Waiting Deploy" is merged work waiting to
ship, and to whoever waits on it as finished as "Done". The tracker cannot say
so (YouTrack does not mark such a state resolved), so the operator lists them.
Read fresh on every use, so a save in Settings needs no restart.
"""

from __future__ import annotations

from collections.abc import Iterable

from kato_core_lib.helpers.kato_settings_store_utils import live_setting, write_kato_settings

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


#: A state is written into a comma-separated setting, so it cannot hold one;
#: and a real ticket state is a short name.
MAX_STATE_CHARS = 80


def add_done_state(state: str) -> list[str]:
    """Count ``state`` as done from now on: added to the setting as written
    (once, whatever its case). Returns the listed states. Raises
    ``ValueError`` for a state that cannot be listed."""
    name = str(state or '').strip()
    if not name or ',' in name or len(name) > MAX_STATE_CHARS:
        raise ValueError(f'{state!r} is not a ticket state that can be listed')
    listed = [entry.strip() for entry in live_setting(DONE_STATES_KEY).split(',') if entry.strip()]
    if name.lower() not in {entry.lower() for entry in listed}:
        listed.append(name)
        write_kato_settings({DONE_STATES_KEY: ', '.join(listed)})
    return listed


def unrecognized_states(states: Iterable[str], known: Iterable[str]) -> list[str]:
    """The ``states`` (as written, once each, in order) that are none of the
    workflow's ``known`` names and are not listed as done: a ticket sitting in
    one reads as "still to do", and a plan waiting on it waits forever."""
    names = {str(entry or '').strip().lower() for entry in known} | extra_done_states()
    found: list[str] = []
    for state in states:
        text = str(state or '').strip()
        if text and text.lower() not in names and text.lower() not in {entry.lower() for entry in found}:
            found.append(text)
    return found
