"""Operator switch: strict git (the default) or local git for the agent.

Settings → Strict git (its own tab). On (the default, and what kato always
did): the agent edits files, stages, stashes and restores; kato makes every
commit, moves every branch and pushes. Off: the agent may also run the history
of its own task branch — commit, amend, rebase, reset, merge, cherry-pick,
revert, tag — so "commit this" or "squash those two" in a chat is done instead
of refused. In both modes kato alone pushes, and only when the operator does.

What each level allows is ``agent_core_lib``'s ``command_floor``; this module
only maps kato's setting onto it. Read FRESH (``live_setting``: settings.json,
then the shell), so a save needs no restart: a running chat picks it up on its
next message. Global — both backends read this one function: Claude at every
spawn (the webserver respawns a chat whose deny list no longer matches), Codex
at the start of every turn (its sandbox opens each repository's ``.git`` only at
local access). Never in Auto (bypassPermissions), where nobody sees a prompt.

Fail closed: only an explicit off value turns strict off. A typo, an empty
value or an unreadable settings file all mean strict.
"""

from __future__ import annotations

from agent_core_lib.agent_core_lib.helpers.command_floor import (
    GIT_ACCESS_LOCAL,
    GIT_ACCESS_STRICT,
)
from kato_core_lib.helpers.kato_settings_store_utils import live_setting

AGENT_GIT_STRICT_KEY = 'KATO_AGENT_GIT_STRICT'

_OFF = ('false', '0', 'no', 'off')


def agent_git_access(env: dict | None = None) -> str:
    """``'local'`` when the operator switched strict git off, else ``'strict'``."""
    if live_setting(AGENT_GIT_STRICT_KEY, env).strip().lower() in _OFF:
        return GIT_ACCESS_LOCAL
    return GIT_ACCESS_STRICT
