"""Operator gate for the autonomous flow's push + pull-request step.

kato's standing policy is that it never publishes on its own: a branch is
pushed and a pull request opened only when the operator asks for it in the
planning UI. The autonomous task flow did not honour that. It paused for
approval only when a task carried the ``kato:wait-before-git-push`` tag, so
every *untagged* task ran straight through ``publish_task_execution`` — push,
PR, summary comment, and a move to "In Review" — with no operator involved.
An opt-IN safety gate leaves the unsafe path as the default, which is the
wrong way round for an irreversible, outward-facing action.

``KATO_AUTO_PUSH_ENABLED`` (default ``false``) inverts that. Unset, the
autonomous flow always stops after implementation + testing and waits for
``approve_push``; the tag still forces the pause, so tagged tasks behave
exactly as before. Set it to ``true`` to restore fully autonomous publishing.

Read FRESH on every check — same reasoning and same store precedence as
:mod:`kato_core_lib.helpers.review_comment_gate_utils`: ``~/.kato/settings.json``
is the live, UI-managed source and ``os.environ`` is the fallback for a key
only ever set in the shell. Going through the boot-time OmegaConf config
would make this restart-only, and an operator turning autonomous publishing
off means *now*, before the next scan tick pushes something.

Scope is the autonomous flow only. The UI's own Push / "Done - Push" buttons
(``push_task``, ``approve_push``) are operator actions by definition and are
never gated — this switch decides whether kato may act *without* being asked.
"""

from __future__ import annotations

import os

from kato_core_lib.helpers.kato_settings_store_utils import read_kato_settings

AUTO_PUSH_ENABLED_KEY = 'KATO_AUTO_PUSH_ENABLED'

# Unset means OFF. This is an opt-IN to autonomous publishing: pushing a
# branch and opening a PR is outward-facing and awkward to walk back, so the
# operator has to ask for it rather than remember to switch it off.
AUTO_PUSH_ENABLED_DEFAULT = 'false'

# Surfaced in the ticket comment kato posts when it parks a finished task, so
# "why didn't kato open my PR?" answers itself without a log dig.
AUTO_PUSH_DISABLED_REASON = (
    'autonomous push and pull-request creation are switched off '
    f'({AUTO_PUSH_ENABLED_KEY}=false)'
)

_TRUTHY = ('true', '1', 'yes', 'on')


def _resolved_value(env: dict) -> str:
    """settings.json (live) → shell env → default-off."""
    try:
        settings = read_kato_settings()
    except Exception:  # noqa: BLE001 - a corrupt settings file must not
        settings = {}  # decide the switch; fall through to env/default.
    for source in (settings, env):
        value = source.get(AUTO_PUSH_ENABLED_KEY)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return AUTO_PUSH_ENABLED_DEFAULT


def auto_push_enabled(env: dict | None = None) -> bool:
    """May the autonomous flow push and open a PR without being asked?

    ``False`` (the default) means it must park the finished task and wait for
    an operator-triggered ``approve_push``.
    """
    env = os.environ if env is None else env
    return _resolved_value(env).lower() in _TRUTHY
