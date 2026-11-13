"""Index of locally stored Grok sessions, for the adoption flow.

Grok keeps every conversation under
``$GROK_HOME/sessions/<url-encoded cwd>/<session id>/`` (see ``history.py``).
This walks that store and parses just enough to let an operator recognise
which conversation is which, so a session started in the Grok CLI can be
handed to the host and resumed rather than started over.

Row shape matches every other transport's index for the fields a picker
draws — id, cwd, mtime, turn count, first/last message — so one UI renders
any backend's sessions.

- **Read-only.** Never writes to the CLI's store.
- **Best-effort.** A malformed line or file is skipped, never raised.
- **Bounded.** Only a capped prefix of each log is scanned for previews.
- ``GROK_HOME`` repoints the root, matching the CLI's own resolution order.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import unquote

from agent_core_lib.agent_core_lib.session.index_utils import (
    MAX_PREVIEW_SCAN_BYTES,
    cap_results,
    clip_preview,
    matches_query,
    parse_jsonl_dict_line,
)
from grok_core_lib.grok_core_lib.session.history import (
    SUMMARY_FILE,
    UPDATE_USER_MESSAGE,
    UPDATES_FILE,
    grok_home,
    session_update,
    update_text,
)


@dataclass(frozen=True)
class GrokSessionMetadata:
    """One row in the session index (``turn_count`` counts user messages)."""

    agent_session_id: str
    cwd: str
    last_modified_epoch: float
    turn_count: int
    first_user_message: str
    last_user_message: str

    def to_dict(self) -> dict:
        return asdict(self)


def default_sessions_root(env: dict | None = None) -> Path:
    """``$GROK_HOME/sessions``, else ``~/.grok/sessions``."""
    return grok_home(env) / 'sessions'


def list_sessions(
    *,
    query: str = '',
    sessions_root: Path | None = None,
    max_results: int = 100,
) -> list[GrokSessionMetadata]:
    """Sessions on disk, most-recently-modified first.

    ``query`` filters case-insensitively across the cwd and both message
    previews. A missing store is an empty list: a host with no Grok CLI simply
    has nothing to adopt.
    """
    root = Path(sessions_root if sessions_root is not None else default_sessions_root()).expanduser()
    if not root.is_dir():
        return []
    try:
        folders = [path for path in root.glob('*/*') if path.is_dir()]
    except OSError:
        return []
    folders.sort(key=_last_modified, reverse=True)
    needle = str(query or '').strip().lower()
    rows: list[GrokSessionMetadata] = []
    for folder in folders:
        row = _read_session(folder)
        if row is None:
            continue
        if needle and not matches_query(
            needle, row.cwd, row.first_user_message, row.last_user_message,
        ):
            continue
        rows.append(row)
        if 0 <= max_results <= len(rows):
            break
    return cap_results(rows, max_results)


def _read_session(folder: Path) -> GrokSessionMetadata | None:
    updates = folder / UPDATES_FILE
    summary = folder / SUMMARY_FILE
    if not updates.is_file() and not summary.is_file():
        return None
    first_user, last_user, turn_count = _user_messages(updates)
    return GrokSessionMetadata(
        agent_session_id=folder.name,
        cwd=_session_cwd(folder, summary),
        last_modified_epoch=_last_modified(folder),
        turn_count=turn_count,
        first_user_message=first_user,
        last_user_message=last_user,
    )


def _session_cwd(folder: Path, summary: Path) -> str:
    """The working directory: the summary's own record, else the group name."""
    try:
        info = json.loads(summary.read_text(encoding='utf-8')).get('info') or {}
        if isinstance(info, dict) and str(info.get('cwd', '') or '').strip():
            return str(info['cwd'])
    except (OSError, ValueError, AttributeError):
        pass
    # A group name too long to encode is a slug + hash, with the real path in
    # a ``.cwd`` file beside the sessions.
    try:
        return (folder.parent / '.cwd').read_text(encoding='utf-8').strip()
    except OSError:
        return unquote(folder.parent.name)


def _user_messages(updates: Path) -> tuple[str, str, int]:
    first = last = ''
    count = 0
    previous_kind = ''
    try:
        with updates.open('r', encoding='utf-8', errors='replace') as handle:
            scanned = 0
            for line in handle:
                scanned += len(line)
                if scanned > MAX_PREVIEW_SCAN_BYTES:
                    break
                record = parse_jsonl_dict_line(line)
                update = session_update(record) if record is not None else None
                kind = update.get('sessionUpdate') if update else ''
                if kind == UPDATE_USER_MESSAGE and previous_kind != UPDATE_USER_MESSAGE:
                    text = clip_preview(update_text(update))
                    if text:
                        count += 1
                        first = first or text
                        last = text
                previous_kind = kind or previous_kind
    except OSError:
        return '', '', 0
    return first, last, count


def _last_modified(folder: Path) -> float:
    stamps = []
    for name in (UPDATES_FILE, SUMMARY_FILE):
        try:
            stamps.append((folder / name).stat().st_mtime)
        except OSError:
            continue
    if stamps:
        return max(stamps)
    try:
        return folder.stat().st_mtime
    except OSError:
        return 0.0
