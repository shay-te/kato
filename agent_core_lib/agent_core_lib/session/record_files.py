"""Where a session record lives on disk, and how it survives a restart.

One JSON file per task under a caller-supplied state directory. Nothing here
knows which CLI produced the session — the record is backend-agnostic, so its
storage is too, and a second transport needs no second copy of these rules.

Two rules are load-bearing, both learned from bugs:

* **Filenames are keyed on the lowercased task id.** Ticket ids arrive with
  disagreeing case (``UNA-1201`` from the platform, ``una-1201`` from disk),
  and a case-sensitive file name means one logical task with two records.
* **Deleting removes every file that maps to the key**, not just the canonical
  path. Records written before the lowercasing existed live under their
  original-case name; unlinking only the canonical path left the legacy file
  behind, and the blanket load below then resurrected the task's tab on every
  restart — the "the task is back after I deleted it" bug.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from utils_core_lib.utils_core_lib.atomic_write import atomic_write_json

from agent_core_lib.agent_core_lib.session.record import (
    SESSION_STATUS_ACTIVE,
    SESSION_STATUS_TERMINATED,
    AgentSessionRecord,
)


def record_key(task_id: str) -> str:
    """The canonical lookup key for a task id: stripped and lowercased."""
    return str(task_id or '').strip().lower()


def record_path(state_dir: Path, task_id: str) -> Path:
    """The JSON file holding ``task_id``'s record.

    Ticket ids are usually filename-safe (``PROJ-123``), but path separators
    are stripped anyway — a task id is external input, and one containing a
    slash would otherwise write outside the state directory.
    """
    safe_name = record_key(task_id).replace('/', '_').replace(os.sep, '_')
    return Path(state_dir) / f'{safe_name}.json'


def write_record(state_dir: Path, record: AgentSessionRecord, *, logger) -> None:
    """Persist ``record`` atomically, so a crash mid-write cannot truncate it."""
    atomic_write_json(
        record_path(state_dir, record.task_id),
        record.to_dict(),
        logger=logger,
        label=f'session record for task {record.task_id}',
    )


def delete_record(state_dir: Path, task_id: str, *, logger) -> None:
    """Remove every stored file that maps to ``task_id`` — see the module note."""
    key = record_key(task_id).replace('/', '_').replace(os.sep, '_')
    targets = {record_path(state_dir, task_id)}
    try:
        for candidate in Path(state_dir).glob('*.json'):
            if candidate.stem.lower() == key:
                targets.add(candidate)
    except OSError:
        # Directory listing failed — fall back to the canonical path alone.
        pass
    for path in targets:
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        except OSError as exc:
            logger.warning(
                'failed to remove session record %s for task %s: %s',
                path, task_id, exc,
            )


def load_records(state_dir: Path, *, logger) -> dict[str, AgentSessionRecord]:
    """Read every stored record, keyed by :func:`record_key`.

    An unreadable or malformed file is skipped with a warning rather than
    failing the load: one corrupt record must not cost the operator every
    other tab. A record found ``active`` is returned ``terminated`` — on
    startup the subprocess behind it is gone, and a tab claiming to be live
    with nothing behind it is worse than an honest dead one.
    """
    records: dict[str, AgentSessionRecord] = {}
    state_dir = Path(state_dir)
    if not state_dir.exists():
        return records
    for path in sorted(state_dir.glob('*.json')):
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning('skipping unreadable session record %s: %s', path, exc)
            continue
        if not isinstance(payload, dict):
            continue
        record = AgentSessionRecord.from_dict(payload)
        if not record.task_id:
            continue
        if record.status == SESSION_STATUS_ACTIVE:
            record.status = SESSION_STATUS_TERMINATED
            record.updated_at_epoch = time.time()
        # Keyed lowercased so a case-mismatched lookup finds it; the record's
        # own ``task_id`` keeps the case it was written with, for display.
        records[record_key(record.task_id)] = record
    return records
