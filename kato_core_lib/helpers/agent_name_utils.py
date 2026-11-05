"""How this kato signs what it posts for the team: whose agent finished a task.

Several developers each run a kato against the same tracker and post to the same
Slack channel, so "UNA-3267 is done" does not say whose agent did it. The name is
the operator's own (Settings → Email & Slack → Agent name, read fresh: a save
applies to the next post), else the developer's name on this computer —
"Shay Tessler's kato", the name their teammates know them by — else the
computer's name.

The developer's name is git's ``user.name``. Some set an email address there
("shay.te@gmail.com's kato" reads badly and posts the address to the channel),
and some set none: then it is the computer account's full name, else the part
before the "@".
"""

from __future__ import annotations

import os
import socket
import subprocess
from collections.abc import Callable
from pathlib import Path

from git_core_lib.git_core_lib.helpers.git_command_utils import build_safe_git_command

from kato_core_lib.helpers.kato_settings_store_utils import live_setting

AGENT_NAME_KEY = 'KATO_AGENT_NAME'

#: A name typed in Settings is shown as it is, up to this length.
AGENT_NAME_MAX_CHARS = 80

_GIT_TIMEOUT_SECONDS = 5


def agent_name(
    env: dict | None = None,
    *,
    git_user_name: Callable[[], str] | None = None,
    account_name: Callable[[], str] | None = None,
    host_name: Callable[[], str] | None = None,
) -> str:
    """The name this kato signs with. Never empty."""
    chosen = ' '.join(live_setting(AGENT_NAME_KEY, env).split())
    if chosen:
        return chosen[:AGENT_NAME_MAX_CHARS]
    person = developer_name(
        (git_user_name or global_git_user_name)(), account_name or os_account_full_name,
    )
    if person:
        return f"{person}'s kato"
    host = (host_name or short_host_name)()
    return f'kato on {host}' if host else 'kato'


def developer_name(git_name: str, account_name: Callable[[], str]) -> str:
    """git's ``user.name``, unless it is empty or an email address: then the
    account's full name, else the address's local part. '' = nothing to go by."""
    if git_name and '@' not in git_name:
        return git_name
    return account_name() or git_name.split('@', 1)[0].strip()


def global_git_user_name() -> str:
    """``git config --global user.name`` ('' when unset or git cannot say),
    through git_core_lib's hardened command like every other git call."""
    command = build_safe_git_command(str(Path.home()), ['config', '--global', '--get', 'user.name'])
    try:
        result = subprocess.run(
            command, stdin=subprocess.DEVNULL, capture_output=True, text=True,
            timeout=_GIT_TIMEOUT_SECONDS, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ''
    if result.returncode != 0:
        return ''
    return ' '.join(result.stdout.split())[:AGENT_NAME_MAX_CHARS]


def os_account_full_name() -> str:
    """The computer account's full name ("Shay Tessler"), where the system
    keeps one (macOS, Linux); '' elsewhere (Windows) or when it is blank."""
    try:
        import pwd
    except ImportError:
        return ''
    try:
        gecos = pwd.getpwuid(os.getuid()).pw_gecos
    except (KeyError, OSError, AttributeError):
        return ''
    # GECOS may carry ",room,phone" after the name.
    return ' '.join(str(gecos or '').split(',', 1)[0].split())[:AGENT_NAME_MAX_CHARS]


def short_host_name() -> str:
    """The computer's name without its domain (``Shays-MacBook-Pro``)."""
    try:
        return socket.gethostname().split('.')[0].strip()
    except OSError:
        return ''
