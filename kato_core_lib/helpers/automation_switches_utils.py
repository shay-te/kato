"""Operator switches for what kato does on its own during a task (Settings → Automation).

Each is ON unless the operator turns it off, read FRESH on every use
(``live_setting``: settings.json, then the shell), so a save needs no restart.
Only an explicit off value turns one off: a typo or an empty value keeps the
behaviour kato always had.

* ``KATO_RUN_TESTS`` — kato's testing step after the agent finishes, and the
  agent being asked to run the tests at all (``run_tests_utils``).
* ``KATO_STOP_CONTAINERS_ON_DONE`` — stopping the Docker containers a task's
  agent started, when the task reaches Done (``task_containers``).
"""

from __future__ import annotations

from kato_core_lib.helpers.kato_settings_store_utils import live_setting

RUN_TESTS_KEY = 'KATO_RUN_TESTS'
STOP_CONTAINERS_ON_DONE_KEY = 'KATO_STOP_CONTAINERS_ON_DONE'

#: Read fresh on every use — saving one needs no restart.
AUTOMATION_SWITCH_KEYS: frozenset[str] = frozenset({RUN_TESTS_KEY, STOP_CONTAINERS_ON_DONE_KEY})

_OFF = ('false', '0', 'no', 'off')


def switch_on(key: str, env: dict | None = None) -> bool:
    """True unless ``key`` is explicitly switched off."""
    return live_setting(key, env).strip().lower() not in _OFF


def run_tests_enabled(env: dict | None = None) -> bool:
    return switch_on(RUN_TESTS_KEY, env)


def stop_containers_on_done(env: dict | None = None) -> bool:
    return switch_on(STOP_CONTAINERS_ON_DONE_KEY, env)
