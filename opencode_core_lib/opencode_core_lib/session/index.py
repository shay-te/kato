"""Index of locally stored OpenCode sessions, for the adoption flow.

OpenCode keeps its sessions in a SQLite database under its data directory, in
a schema of its own; this lists them through the CLI's documented
``opencode session list --format json`` instead, so a session started in the
OpenCode CLI can be handed to the host and resumed rather than started over.

Row shape matches every other transport's index for the fields a picker
draws — id, cwd, mtime, turn count, first/last message — so one UI renders
any backend's sessions. The list names a session by its TITLE, not its
messages, so ``first_user_message`` carries the title and ``turn_count`` is 0
(unknown, not none).

- **Read-only.** Never writes to the CLI's store.
- **Best-effort.** A CLI that is missing, fails or prints something else
  answers an empty list, never an error.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from agent_core_lib.agent_core_lib.session.index_utils import (
    cap_results,
    clip_preview,
    matches_query,
)
from opencode_core_lib.opencode_core_lib.session.history import run_store_command


@dataclass(frozen=True)
class OpenCodeSessionMetadata:
    """One row in the session index."""

    agent_session_id: str
    cwd: str
    last_modified_epoch: float
    turn_count: int
    first_user_message: str
    last_user_message: str

    def to_dict(self) -> dict:
        return asdict(self)


def default_sessions_root(env: dict | None = None) -> Path:
    """OpenCode's data directory: ``$XDG_DATA_HOME/opencode``, else ``~/.local/share/opencode``."""
    source = os.environ if env is None else env
    base = str(source.get('XDG_DATA_HOME', '') or '').strip()
    return (Path(base).expanduser() if base else Path.home() / '.local' / 'share') / 'opencode'


def list_sessions(
    *,
    query: str = '',
    sessions_root: Path | None = None,
    max_results: int = 100,
) -> list[OpenCodeSessionMetadata]:
    """Sessions the CLI lists, most-recently-updated first.

    ``sessions_root`` is accepted for signature parity and unused: the store
    is a database read through the CLI, not a folder to walk.
    """
    del sessions_root  # see docstring
    count = max(1, int(max_results)) if max_results and max_results > 0 else 100
    text = run_store_command(['session', 'list', '--format', 'json', '--max-count', str(count)])
    needle = str(query or '').strip().lower()
    rows = [
        row for row in rows_from_listing(text)
        if not needle or matches_query(needle, row.cwd, row.first_user_message)
    ]
    rows.sort(key=lambda row: row.last_modified_epoch, reverse=True)
    return cap_results(rows, max_results)


def rows_from_listing(text: str) -> list[OpenCodeSessionMetadata]:
    try:
        data = json.loads(text) if text else None
    except ValueError:
        return []
    sessions = data.get('sessions') if isinstance(data, dict) else data
    rows = []
    for session in sessions if isinstance(sessions, list) else ():
        row = _row(session)
        if row is not None:
            rows.append(row)
    return rows


def _row(session: Any) -> OpenCodeSessionMetadata | None:
    if not isinstance(session, dict):
        return None
    session_id = str(session.get('id', '') or '').strip()
    if not session_id:
        return None
    times = session.get('time') if isinstance(session.get('time'), dict) else {}
    updated = times.get('updated') or times.get('created') or 0
    location = session.get('location') if isinstance(session.get('location'), dict) else {}
    return OpenCodeSessionMetadata(
        agent_session_id=session_id,
        cwd=str(session.get('directory', '') or location.get('directory', '') or ''),
        last_modified_epoch=float(updated) / 1000 if isinstance(updated, (int, float)) else 0.0,
        turn_count=0,
        first_user_message=clip_preview(str(session.get('title', '') or '')),
        last_user_message='',
    )
