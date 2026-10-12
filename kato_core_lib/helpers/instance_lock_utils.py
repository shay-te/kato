"""One kato per ``~/.kato``.

Two katos on the same ``~/.kato`` both scan the same tasks, run agents in the
same workspaces and write the same stores. Their webserver port used to be the
only thing keeping a second one out — it could not bind, and stopped — but the
desktop app picks a free port from its range, so a second kato would simply
get another one. This lock holds whatever the port: a second kato refuses to
start and says which one owns the folder.

The lock is the OS's (``hold_exclusive_file_lock``), so a kato that crashed
leaves nothing to clean up; who holds it is written beside it for the message.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from utils_core_lib.utils_core_lib.atomic_write import atomic_write_json
from utils_core_lib.utils_core_lib.file_lock import hold_exclusive_file_lock

from kato_core_lib.helpers.kato_paths_utils import kato_home_path
from kato_core_lib.helpers.logging_utils import configure_logger

INSTANCE_LOCK_ENV = 'KATO_INSTANCE_LOCK_PATH'

# Set by a kato restarting itself in place, for the kato it starts: that one
# waits for the lock (``RESTART_GRACE_SECONDS``) — Python emulates ``os.execv``
# on Windows, so the old process may still hold it for a moment. Any other
# start is refused at once: waiting only delays "another kato is running".
INSTANCE_RESTART_ENV = 'KATO_INSTANCE_RESTARTING'
RESTART_GRACE_SECONDS = 10.0
_POLL_SECONDS = 0.25

# This process's lock, once taken: held until it exits. A second ``main()`` in
# the same process (the tests, a restart in place) keeps it rather than
# refusing itself.
_held_descriptor: int | None = None


def instance_lock_path() -> Path:
    """``~/.kato/kato.instance`` — who holds the lock; the lockfile is beside it."""
    return kato_home_path('kato.instance', env_key=INSTANCE_LOCK_ENV)


def hold_instance_lock(wait_seconds: float | None = None) -> str:
    """Take the lock for this process\'s life. '' once held; otherwise why not.

    ``wait_seconds``: how long another holder may take to let go first — by
    default only a restarting kato waits (``INSTANCE_RESTART_ENV``).

    A folder that cannot be locked at all (a network drive without lock
    support) is not "another kato": kato says so and runs, rather than refusing
    forever on a lock nobody holds.
    """
    global _held_descriptor
    if _held_descriptor is not None:
        return ''
    restarting = bool(os.environ.pop(INSTANCE_RESTART_ENV, ''))
    if wait_seconds is None:
        wait_seconds = RESTART_GRACE_SECONDS if restarting else 0.0
    path = instance_lock_path()
    deadline = time.monotonic() + max(0.0, wait_seconds)
    try:
        descriptor = hold_exclusive_file_lock(path)
        while descriptor is None and time.monotonic() < deadline:
            time.sleep(_POLL_SECONDS)
            descriptor = hold_exclusive_file_lock(path)
    except OSError as exc:
        configure_logger('instance_lock').warning(
            'kato cannot lock %s (%s), so it cannot tell whether another kato runs on '
            'this folder — running anyway', path, exc,
        )
        return ''
    if descriptor is None:
        return _already_running_message(path)
    _held_descriptor = descriptor
    atomic_write_json(path, {
        'pid': os.getpid(),
        'port': str(os.environ.get('KATO_WEBSERVER_PORT', '') or '5050'),
        'started_at': time.time(),
    }, label='kato instance')
    return ''


def _already_running_message(path: Path) -> str:
    try:
        owner = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        owner = {}
    owner = owner if isinstance(owner, dict) else {}
    details = ', '.join(
        f'{label} {owner[key]}' for key, label in (('pid', 'pid'), ('port', 'port')) if owner.get(key)
    )
    who = f' ({details})' if details else ''
    return (
        f'another kato is already running on {path.parent}{who} — two would work the '
        'same tasks. Stop it (Ctrl+C in its terminal, or quit the Kato app), then start '
        'this one.'
    )
