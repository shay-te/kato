"""Save quick start's three settings in one write.

``KATO_AGENT_BACKEND=claude``, the code folder (``REPOSITORY_ROOT_PATH``) and —
only when no ticket system is set yet — ``KATO_ISSUE_PLATFORM=local``. Unset,
those two defaults are OpenHands and YouTrack, both needing keys a first run
does not have, so the minimum is written, never left to a default.

The folder must exist; kato creates it only when asked, and only inside the home
folder (the suggested ``~/code`` on a computer with no repositories yet).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from kato_core_lib.helpers.issue_platform_utils import NO_TRACKER_ISSUE_PLATFORM


class QuickStartError(Exception):
    """The folder cannot be used; the message says why."""


def save_quick_start(
    folder: str,
    *,
    create: bool,
    env: dict,
    write: Callable[[dict], object],
    home: Path | None = None,
) -> dict:
    """Write the settings; returns what was saved."""
    path = _usable_folder(folder, create=create, home=home or Path.home())
    updates = {'KATO_AGENT_BACKEND': 'claude', 'REPOSITORY_ROOT_PATH': str(path)}
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
