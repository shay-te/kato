"""Save quick start's settings in one write.

* ``KATO_CHAT_AGENT``: the agent the operator picked and set up — new tasks'
  chats start on it (``chat_agent_utils``).
* ``KATO_AGENT_BACKEND=claude``: what tickets kato runs by itself would use (only
  Claude or OpenHands can). Unset it means OpenHands, which needs keys a first
  run does not have, so it is written, never left to the default.
* ``REPOSITORY_ROOT_PATH``: the code folder the operator picked.
* ``KATO_ISSUE_PLATFORM=local`` — only when no ticket system is set yet (unset
  means YouTrack, which needs keys too).

The folder must exist; kato creates it only when asked, and only inside the home
folder (the suggested ``~/code`` on a computer with no repositories yet).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from kato_core_lib.helpers.chat_agent_utils import CHAT_AGENT_KEY, normalized_chat_agent
from kato_core_lib.helpers.issue_platform_utils import NO_TRACKER_ISSUE_PLATFORM


class QuickStartError(Exception):
    """The folder cannot be used; the message says why."""


def save_quick_start(
    folder: str,
    *,
    agent: str,
    create: bool,
    env: dict,
    write: Callable[[dict], object],
    home: Path | None = None,
) -> dict:
    """Write the settings; returns what was saved."""
    chosen = normalized_chat_agent(agent)
    if not chosen:
        raise QuickStartError(f'pick the agent you use — {agent!r} is not one kato knows')
    path = _usable_folder(folder, create=create, home=home or Path.home())
    updates = {
        CHAT_AGENT_KEY: chosen, 'KATO_AGENT_BACKEND': 'claude', 'REPOSITORY_ROOT_PATH': str(path),
    }
    if not str(env.get('KATO_ISSUE_PLATFORM') or '').strip():
        updates['KATO_ISSUE_PLATFORM'] = NO_TRACKER_ISSUE_PLATFORM
    write(updates)
    return updates


def _usable_folder(folder: str, *, create: bool, home: Path) -> Path:
    text = str(folder or '').strip()
    if not text:
        raise QuickStartError('pick the folder your code is in')
    try:
        path = Path(text).expanduser().resolve()
    except (OSError, ValueError) as exc:
        raise QuickStartError(f'not a usable folder: {exc}') from exc
    if path.exists():
        if not path.is_dir():
            raise QuickStartError(f'{path} is a file, not a folder')
        return path
    resolved_home = home.expanduser().resolve()
    if not create:
        raise QuickStartError(f'{path} does not exist')
    if resolved_home not in path.parents:
        raise QuickStartError(f'kato only creates a folder inside {resolved_home}')
    try:
        path.mkdir(parents=True)
    except OSError as exc:
        raise QuickStartError(f'could not create {path}: {exc}') from exc
    return path
