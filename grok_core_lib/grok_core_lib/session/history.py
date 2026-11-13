"""Replay a Grok chat from the CLI's own session store.

A live chat's events live in its session object's memory, so a chat survived
a page reload but not a restart of the process hosting it. Grok writes every
session to disk; this reads it back.

Layout, as Grok Build documents it::

    $GROK_HOME/sessions/<url-encoded cwd>/<session id>/
        summary.json     metadata: title, timestamps, model, message counts
        updates.jsonl    the authoritative log: one ACP session update per line

Each ``updates.jsonl`` line is an ACP session update naming its kind in
``sessionUpdate`` (``user_message_chunk``, ``agent_message_chunk``,
``agent_thought_chunk``, ``tool_call`` …). Only the operator's and the agent's
messages are replayed — the same two kinds the other transports replay —
returned in the live stream's shape so the chat renders a replayed turn
through the one path it renders a live one.

The line layout around ``sessionUpdate`` is not specified in Grok's docs, so
the reader takes the update at the top level or under ``update`` /
``params.update``, and returns ``[]`` for anything it cannot read. A chat that
renders empty is better than one that fails to open.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from agent_core_lib.agent_core_lib.session.index_utils import (
    parse_jsonl_dict_line,
    text_from_content,
)
from agent_core_lib.agent_core_lib.session.turn_events import agent_message, user_message

#: Cap on returned events, matching the other transports' replay. A very long
#: chat is truncated from the FRONT so the most recent exchange survives.
DEFAULT_MAX_EVENTS = 5000

UPDATE_USER_MESSAGE = 'user_message_chunk'
UPDATE_AGENT_MESSAGE = 'agent_message_chunk'
UPDATES_FILE = 'updates.jsonl'
SUMMARY_FILE = 'summary.json'


def grok_home(env: dict | None = None) -> Path:
    """``$GROK_HOME``, else ``~/.grok`` — the CLI's own resolution order."""
    source = os.environ if env is None else env
    configured = str(source.get('GROK_HOME', '') or '').strip()
    return Path(configured).expanduser() if configured else Path.home() / '.grok'


def find_session_dir(agent_session_id: str, *, home: Path | str | None = None) -> Path | None:
    """The session's folder (under whichever working-directory group), or None."""
    session_id = str(agent_session_id or '').strip()
    if not session_id or '/' in session_id or '\\' in session_id:
        return None
    root = (Path(home).expanduser() if home is not None else grok_home()) / 'sessions'
    if not root.is_dir():
        return None
    try:
        matches = [path for path in root.glob(f'*/{session_id}') if path.is_dir()]
    except OSError:
        return None
    if not matches:
        return None
    return max(matches, key=_mtime)


def session_update(record: dict) -> dict | None:
    """The ACP update a stored line carries, wherever the line put it."""
    for candidate in (record, record.get('update'), (record.get('params') or {}).get('update')):
        if isinstance(candidate, dict) and candidate.get('sessionUpdate'):
            return candidate
    return None


def update_text(update: dict) -> str:
    content = update.get('content')
    return text_from_content([content] if isinstance(content, dict) else content)


def load_history_events(
    agent_session_id: str,
    *,
    home: Path | str | None = None,
    max_events: int = DEFAULT_MAX_EVENTS,
) -> list[dict[str, Any]]:
    """The chat's turns, in the live stream's wire shape; ``[]`` if unreadable.

    Consecutive chunks of one message are joined into one event.
    """
    directory = find_session_dir(agent_session_id, home=home)
    if directory is None:
        return []
    messages: list[tuple[str, str, Any]] = []
    try:
        with (directory / UPDATES_FILE).open('r', encoding='utf-8', errors='replace') as handle:
            for line in handle:
                record = parse_jsonl_dict_line(line)
                update = session_update(record) if record is not None else None
                kind = update.get('sessionUpdate') if update else ''
                if kind not in (UPDATE_USER_MESSAGE, UPDATE_AGENT_MESSAGE):
                    continue
                text = update_text(update)
                if not text:
                    continue
                if messages and messages[-1][0] == kind:
                    previous = messages[-1]
                    messages[-1] = (kind, previous[1] + text, previous[2])
                else:
                    messages.append((kind, text, record.get('timestamp')))
    except OSError:
        return []
    events = []
    for kind, text, timestamp in messages:
        event = user_message(text.strip()) if kind == UPDATE_USER_MESSAGE else agent_message(text.strip())
        if timestamp:
            event['timestamp'] = timestamp
        events.append(event)
    if max_events >= 0 and len(events) > max_events:
        events = events[-max_events:] if max_events else []
    return events


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0
