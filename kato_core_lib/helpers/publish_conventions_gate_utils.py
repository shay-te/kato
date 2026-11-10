"""Operator switch: should kato's commit messages and PR text follow the
repository's own ``CLAUDE.md``/``AGENTS.md`` conventions?

Off by default — this changes text that lands in a shared repository's
history and pull requests, so it is an opt-in, not a silent default change.
Read FRESH on every check, same precedence as every other live setting
(``kato_settings_store_utils.live_setting``): ``~/.kato/settings.json``
first, then the shell environment — a Settings-drawer change applies to the
very next publish, no restart.
"""

from __future__ import annotations

from kato_core_lib.helpers.kato_settings_store_utils import live_setting

PUBLISH_FOLLOWS_PROJECT_CONVENTIONS_KEY = 'KATO_PUBLISH_FOLLOWS_PROJECT_CONVENTIONS'

_TRUTHY = ('true', '1', 'yes', 'on')


def publish_follows_project_conventions(env: dict | None = None) -> bool:
    return live_setting(PUBLISH_FOLLOWS_PROJECT_CONVENTIONS_KEY, env).strip().lower() in _TRUTHY
