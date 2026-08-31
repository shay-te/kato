"""Index of locally-stored Codex sessions, for the adoption flow.

The Codex CLI persists every conversation as a rollout transcript under
``$CODEX_HOME/sessions/<YYYY>/<MM>/<DD>/rollout-<timestamp>-<thread_id>.jsonl``.
This module walks that store and parses just enough metadata for an operator
to recognise which conversation is which.

It exists because adoption was Claude-only. A developer mid-conversation in
the Codex CLI who wanted to hand the work to the host had no way to say "carry
on from THAT session" — the host would spawn a fresh one and the context was,
from the developer's point of view, gone. The picker that offers those
sessions needs a list, and listing is the one thing ``history.py`` could not
do: it can find a rollout when you already know the id, which is the opposite
problem.

Row shape deliberately matches the other transports' indexes for the fields a
picker draws — id, cwd, mtime, turn count, first/last message — so one UI
renders either backend's sessions without a per-backend branch. It is not an
exact mirror: Claude's rows also carry a ``transcript_path``, which has no
Codex equivalent because a rollout is found by id rather than by location.

Design notes:

- **Read-only.** Never writes to the CLI's store. The transcript belongs to
  Codex; this reads metadata.
- **Best-effort parsing.** A malformed line is skipped, never raised. A
  transcript being written while it is read is treated as "what we got is
  what is there" — a corrupt store must not break the picker.
- **Bounded.** ``session_meta`` is the FIRST line, so cwd and id cost one
  read. Only a capped prefix is scanned for message previews; a long
  conversation must not make the dropdown slow.
- **Override path for tests.** ``CODEX_HOME`` repoints the root, matching the
  CLI's own resolution order.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from agent_core_lib.agent_core_lib.session.index_utils import (
    MAX_PREVIEW_SCAN_BYTES,
    cap_results,
    clip_preview,
    matches_query,
    parse_jsonl_dict_line,
    text_from_content,
)
from codex_core_lib.codex_core_lib.session.history import codex_home

# ``rollout-<ISO timestamp>-<thread id>.jsonl``, e.g.
# ``rollout-2026-08-30T10-00-00-0199a1b2-c3d4-7e8f-9012-3456789abcde.jsonl``.
# The id is the tail, and reading it here is the fallback for a transcript
# with no readable ``session_meta`` — a rollout truncated at byte zero still
# has a name.
#
# The id is matched as a full UUID rather than as "trailing id-ish
# characters". The timestamp is made of digits and dashes too, so a loose
# tail pattern splits inside it and yields ``00-00-<uuid>`` — an id that
# resolves to nothing. Being strict about the shape puts the boundary where
# it actually is.
_ROLLOUT_NAME = re.compile(
    r'^rollout-.*-('
    r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}'
    r'-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'
    r')\.jsonl$',
)


@dataclass(frozen=True)
class CodexSessionMetadata:
    """One row in the session index.

    ``last_modified_epoch`` is the file mtime — the operator sorts by it to
    find "the session I was just in". ``turn_count`` counts user messages, so
    it is a rough proxy for conversation depth rather than an exact count.
    """

    agent_session_id: str
    cwd: str
    last_modified_epoch: float
    turn_count: int
    first_user_message: str
    last_user_message: str

    def to_dict(self) -> dict:
        return asdict(self)


def default_sessions_root(env: dict | None = None) -> Path:
    """``$CODEX_HOME/sessions``, else ``~/.codex/sessions``."""
    return codex_home(env) / 'sessions'


def list_sessions(
    *,
    query: str = '',
    sessions_root: Path | None = None,
    max_results: int = 100,
) -> list[CodexSessionMetadata]:
    """Rollouts on disk, most-recently-modified first.

    ``query`` filters case-insensitively across the cwd and both message
    previews — the three things an operator actually recognises a
    conversation by. A missing store is an empty list, not an error: a host
    with no Codex CLI simply has nothing to adopt.
    """
    root = sessions_root if sessions_root is not None else default_sessions_root()
    root = Path(root).expanduser()
    if not root.is_dir():
        return []
    try:
        paths = [p for p in root.rglob('rollout-*.jsonl') if p.is_file()]
    except OSError:
        return []

    # Sort by mtime BEFORE parsing, so a store with thousands of rollouts
    # only parses the page the operator can actually see.
    try:
        paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        pass

    needle = str(query or '').strip().lower()
    rows: list[CodexSessionMetadata] = []
    for path in paths:
        row = _read_session(path)
        if row is None:
            continue
        if needle and not _matches(row, needle):
            continue
        rows.append(row)
        if 0 <= max_results <= len(rows):
            break
    return cap_results(rows, max_results)


def _matches(row: CodexSessionMetadata, needle: str) -> bool:
    return matches_query(
        needle, row.cwd, row.first_user_message, row.last_user_message,
    )


def _read_session(path: Path) -> CodexSessionMetadata | None:
    """Parse one rollout into a row, or None when it is unusable."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None

    session_id = _id_from_name(path.name)
    cwd = ''
    first_user = ''
    last_user = ''
    turn_count = 0

    try:
        with path.open('r', encoding='utf-8', errors='replace') as handle:
            scanned = 0
            for line in handle:
                scanned += len(line)
                if scanned > MAX_PREVIEW_SCAN_BYTES:
                    break
                record = parse_jsonl_dict_line(line)
                if record is None:
                    continue
                kind = record.get('type')
                if kind == 'session_meta':
                    payload = record.get('payload')
                    if isinstance(payload, dict):
                        cwd = str(payload.get('cwd', '') or '')
                        # The id inside the file wins over the filename: a
                        # renamed or copied rollout still knows its own id.
                        session_id = str(payload.get('id', '') or '') or session_id
                    continue
                text = _user_text(record)
                if not text:
                    continue
                turn_count += 1
                if not first_user:
                    first_user = text
                last_user = text
    except OSError:
        return None

    if not session_id:
        return None
    return CodexSessionMetadata(
        agent_session_id=session_id,
        cwd=cwd,
        last_modified_epoch=mtime,
        turn_count=turn_count,
        first_user_message=first_user,
        last_user_message=last_user,
    )


def _id_from_name(name: str) -> str:
    match = _ROLLOUT_NAME.match(str(name or ''))
    return match.group(1) if match else ''


def _user_text(record: dict) -> str:
    """The trimmed text of a USER message record, or ''.

    Only user messages: the operator recognises a conversation by what they
    asked, and counting the agent's replies would make ``turn_count`` a
    measure of verbosity.
    """
    if record.get('type') != 'response_item':
        return ''
    payload = record.get('payload')
    if not isinstance(payload, dict) or payload.get('type') != 'message':
        return ''
    if str(payload.get('role', '') or '') != 'user':
        return ''
    return clip_preview(text_from_content(payload.get('content')))
