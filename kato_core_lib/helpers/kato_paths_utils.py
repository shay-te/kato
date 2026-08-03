"""Where kato keeps its state on disk.

Every path under ``~/.kato/`` resolves through here. The rule is one line long
— *a configured path is expanded before it is used* — and it is centralised
because it kept NOT being followed. Four call sites had grown their own
resolver and three of them dropped a step:

    resolver                             strips blanks   expands ``~``
    kato_home_path (this module)         yes             yes
    hooks/config._resolve_path           yes             NO
    kato_core_lib session state_dir      yes             NO
    webserver session state_dir          NO              NO

Neither omission raises. ``KATO_HOOKS_CONFIG=~/hooks.json`` produced a
relative directory literally named ``~``, which does not exist, so the loader
read it as *no hooks configured* and booted with the hook chain silently
disabled — a fail-open on a control the boot path is supposed to fail closed
on. A blank-but-present ``KATO_SESSION_STATE_DIR`` was truthy in the webserver
copy and put session metadata in a directory named after a space, so the UI
and the orchestrator disagreed about where sessions lived.
"""

from __future__ import annotations

import os
from pathlib import Path

SESSION_STATE_DIR_ENV_KEY = 'KATO_SESSION_STATE_DIR'


def configured_path(value: object) -> Path | None:
    """An operator-supplied path, expanded — or ``None`` when unset.

    Blank and whitespace-only values are "unset", so a variable exported empty
    by a wrapper script falls through to the default instead of resolving to
    the current directory.
    """
    text = str(value or '').strip()
    if not text:
        return None
    return Path(text).expanduser()


def kato_home_path(filename: str, *, env_key: str) -> Path:
    """Resolve a file under ``~/.kato/`` with an env-var override.

    Honours ``$<env_key>`` first — used by tests and by operators who keep
    their ``.kato`` dir somewhere non-standard — and otherwise falls back to
    ``~/.kato/<filename>``.
    """
    override = configured_path(os.environ.get(env_key))
    if override is not None:
        return override
    return Path.home() / '.kato' / filename


def kato_session_state_dir(explicit: object = '') -> str:
    """The agent-session metadata directory.

    Precedence: ``explicit`` → ``$KATO_SESSION_STATE_DIR`` → ``~/.kato/sessions``.
    Returned as a string because the session managers take it as one.

    The orchestrator and the webserver each resolve this independently (the
    webserver can run without a live orchestrator), so they must agree
    exactly — a disagreement splits session metadata across two directories
    and the UI stops seeing the chats the agent is writing.
    """
    override = configured_path(explicit)
    if override is not None:
        return str(override)
    return str(kato_home_path('sessions', env_key=SESSION_STATE_DIR_ENV_KEY))
