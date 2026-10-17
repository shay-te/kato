"""Who this kato is, for a plan several katos can see.

A shared plan is driven by one kato: the one that approved it, or one that took
it over. Two katos can work as the same tracker account (a shared bot, one
person on two machines), so a kato is told apart by an id of its own, made once
and kept in its home. The tracker user it works as is the account its token
belongs to — the author the tracker puts on the plan's record.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from utils_core_lib.utils_core_lib.atomic_write import atomic_write_json

from kato_core_lib.helpers.late_binding import provider_for

HOST_ID_FILENAME = 'host.json'


class KatoOwnership(object):
    def __init__(self, *, task_service, root: Path) -> None:
        self._get_task_service = provider_for(task_service)
        self._root = Path(root)
        self._identity = ''
        self._user = ''

    def identity(self) -> str:
        if not self._identity:
            self._identity = _host_id(self._root / HOST_ID_FILENAME)
        return self._identity

    def user(self) -> str:
        """The tracker account this kato's token works as."""
        if not self._user:
            tracker = self._get_task_service().subtask_tracker()
            if tracker is None:
                raise RuntimeError('the issue tracker cannot hold sub-tasks')
            self._user = str(tracker.current_user() or '')
            if not self._user:
                raise RuntimeError('the issue tracker did not say which account this is')
        return self._user


def _host_id(path: Path) -> str:
    """This kato's id, made once. A file that can't be read is replaced."""
    try:
        found = str(json.loads(path.read_text(encoding='utf-8')).get('id', '') or '')
    except (OSError, ValueError, AttributeError):
        found = ''
    if found:
        return found
    made = uuid.uuid4().hex
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, {'id': made}, raise_on_error=True)
    return made
