"""Tasks held in Plan by the ``kato:wait-planning`` tag.

The tag means "discuss, don't edit", and kato honoured it on exactly one
spawn: the hold session it opens when it first sees the tag. Every later
spawn — the operator's next message after that session went idle, a comment
run, a restart — went through the ordinary spawn path with no mode, fell back
to the configured default (acceptEdits), and the composer showed "Edit
automatically". The agent then edited files "even while I am discussing things
with him".

So the tag is recorded here as a HOLD, kept apart from the operator's own mode
pick in ``plan_mode_store``: the hold is kato's reading of the ticket, the pick
is the operator's choice, and releasing one must not erase the other. While a
task is held every spawn runs ``--permission-mode plan`` whatever the pick says
(:func:`held_permission_mode`).

The hold is the task's STARTING mode, not a lock. Reported: "dont block me from
changing modes on the fly ... kato will consider this plan mode only when the
task is initializing then I can change it to whatever I want". When the
operator picks another mode the hold YIELDS (:func:`yield_planning_hold`): the
pick wins, and the next scan — which still sees the tag — does not re-engage
it. A yielded hold is forgotten once the tag leaves the ticket, so adding the
tag again later holds the task in Plan afresh.

Stored at ``~/.kato/planning_holds.json`` (override via
``KATO_PLANNING_HOLD_PATH``) as a sorted list of held task ids, with the
yielded ones in the sibling ``planning_holds.yielded.json``, so both survive a
restart. Ids match case-insensitively, like the forgotten-task store: the
tracker and the UI do not always agree on case.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from utils_core_lib.utils_core_lib.atomic_write import atomic_write_json
from kato_core_lib.helpers.kato_paths_utils import kato_home_path
from kato_core_lib.helpers.plan_mode_store import PLAN_MODE

_ENV_KEY = 'KATO_PLANNING_HOLD_PATH'
_FILENAME = 'planning_holds.json'

# Read-modify-write against the whole file: without this, two scan workers
# recording holds at the same moment can both read the old list and one of the
# holds is lost. Same pattern as plan_mode_store.
_lock = threading.Lock()


def _path() -> Path:
    return kato_home_path(_FILENAME, env_key=_ENV_KEY)


def _yield_path() -> Path:
    held = _path()
    return held.with_name(f'{held.stem}.yielded{held.suffix}')


def _norm(task_id: object) -> str:
    return str(task_id or '').strip()


def _read_ids(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return set()
    if not isinstance(data, list):
        return set()
    return {_norm(item) for item in data if _norm(item)}


def _contains(ids: set[str], task: str) -> bool:
    wanted = task.lower()
    return any(item.lower() == wanted for item in ids)


def _without(ids: set[str], task: str) -> set[str]:
    return {item for item in ids if item.lower() != task.lower()}


def _write_ids(path: Path, ids: set[str]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    atomic_write_json(path, sorted(ids))


def read_planning_holds() -> set[str]:
    """Every held task id (empty when none / unreadable)."""
    return _read_ids(_path())


def task_is_planning_held(task_id: object) -> bool:
    """Whether ``task_id`` is held in Plan by its planning tag."""
    task = _norm(task_id)
    return bool(task) and _contains(read_planning_holds(), task)


def set_planning_hold(task_id: object, held: bool) -> bool:
    """Record (``held``) or release a task's hold; True when that changed it.

    A hold the operator yielded stays yielded while the tag is still on the
    ticket — recording it again would drag the task back into Plan on every
    scan. Releasing (the tag is gone) forgets the yield too.

    Writes only on a change, so calling it for every task on every scan costs
    one small read.
    """
    task = _norm(task_id)
    if not task:
        return False
    with _lock:
        holds = read_planning_holds()
        yielded = _read_ids(_yield_path())
        if held:
            if _contains(holds, task) or _contains(yielded, task):
                return False
            _write_ids(_path(), holds | {task})
            return True
        changed = False
        if _contains(yielded, task):
            _write_ids(_yield_path(), _without(yielded, task))
        if _contains(holds, task):
            _write_ids(_path(), _without(holds, task))
            changed = True
    return changed


def yield_planning_hold(task_id: object) -> bool:
    """The operator picked a mode: stop holding ``task_id`` in Plan.

    True when a hold was yielded. The tag stays on the ticket — kato does not
    edit the tracker for a mode pick — and it holds nothing until it is removed
    and added again.
    """
    task = _norm(task_id)
    if not task:
        return False
    with _lock:
        holds = read_planning_holds()
        if not _contains(holds, task):
            return False
        _write_ids(_yield_path(), _read_ids(_yield_path()) | {task})
        _write_ids(_path(), _without(holds, task))
    return True


def held_permission_mode(task_id: object, requested: object = '') -> str:
    """``plan`` while ``task_id`` is held, otherwise ``requested`` unchanged.

    The one rule every spawn and the composer's mode read go through.
    """
    if task_is_planning_held(task_id):
        return PLAN_MODE
    return _norm(requested)
