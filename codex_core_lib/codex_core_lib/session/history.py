"""Replay a Codex chat from the CLI's own rollout transcript.

The live event log lives in the session object's memory, so a chat survived a
page reload but NOT a restart of the process hosting it — the operator came
back to an empty Codex tab. The CLI already writes every turn to disk; this
reads it back.

Layout, as written by ``codex exec``::

    $CODEX_HOME/sessions/<YYYY>/<MM>/<DD>/rollout-<timestamp>-<thread_id>.jsonl

The thread id in the filename is the id the orchestrator stores as the
chat's ``agent_session_id``, which is what makes a rollout findable at all — nothing
inside the file indexes by task.

Events are returned in the SAME wire shape the live stream emits, so the chat
renders a replayed conversation through exactly the path it renders a live
one. Anything else would mean a second renderer that drifts.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

#: Payload roles worth showing. ``developer`` is the injected system preamble
#: (permissions, AGENTS.md) — machinery the operator did not write and did not
#: ask to read.
_VISIBLE_ROLES = ('user', 'assistant')

#: Cap on returned events, matching the other transport's replay. A very long
#: chat is truncated from the FRONT so the most recent exchange survives.
DEFAULT_MAX_EVENTS = 5000


def codex_home(env: dict | None = None) -> Path:
    """``$CODEX_HOME``, else ``~/.codex`` — the CLI's own resolution order."""
    source = os.environ if env is None else env
    configured = str(source.get('CODEX_HOME', '') or '').strip()
    return Path(configured).expanduser() if configured else Path.home() / '.codex'


def find_rollout_path(
    agent_session_id: str, *, home: Path | str | None = None,
) -> Path | None:
    """The rollout file for ``agent_session_id``, or None.

    Matched on the filename's trailing thread id rather than by reading each
    file: a sessions directory accumulates one file per chat ever started,
    and opening them all to find one is the difference between a chat that
    loads and a chat that hangs.
    """
    thread_id = str(agent_session_id or '').strip()
    if not thread_id:
        return None
    root = Path(home).expanduser() if home is not None else codex_home()
    sessions = root / 'sessions'
    if not sessions.is_dir():
        return None
    suffix = f'-{thread_id}.jsonl'
    try:
        matches = [
            path for path in sessions.rglob(f'*{suffix}') if path.is_file()
        ]
    except OSError:
        return None
    if not matches:
        return None
    # A thread id is unique, but a resumed chat can be written more than once;
    # the newest file is the one that holds the whole conversation.
    return max(matches, key=lambda path: path.stat().st_mtime)


def load_history_events(
    agent_session_id: str,
    *,
    home: Path | str | None = None,
    max_events: int = DEFAULT_MAX_EVENTS,
) -> list[dict]:
    """The chat's turns, in the live stream's wire shape.

    Returns ``[]`` for anything unreadable — a missing rollout, a truncated
    line, a file being written as it is read. A chat that renders empty is a
    great deal better than one that fails to open.
    """
    path = find_rollout_path(agent_session_id, home=home)
    if path is None:
        return []
    events: list[dict] = []
    try:
        with path.open('r', encoding='utf-8', errors='replace') as handle:
            for line in handle:
                event = _event_from_line(line)
                if event is not None:
                    events.append(event)
    except OSError:
        return []
    if max_events >= 0 and len(events) > max_events:
        # From the FRONT: the tail is the part the operator was reading.
        events = events[-max_events:]
    return events


def _event_from_line(line: str) -> dict | None:
    try:
        record = json.loads(line)
    except (ValueError, TypeError):
        return None
    if not isinstance(record, dict) or record.get('type') != 'response_item':
        return None
    payload = record.get('payload')
    if not isinstance(payload, dict) or payload.get('type') != 'message':
        return None
    role = str(payload.get('role', '') or '')
    if role not in _VISIBLE_ROLES:
        return None
    text = _text_from_content(payload.get('content'))
    if not text:
        return None
    if role == 'user':
        # The persistent-process transport's ``user`` shape — the chat
        # already renders it, so a replayed prompt needs no new branch.
        return {
            'type': 'user',
            'message': {'content': [{'type': 'text', 'text': text}]},
        }
    return {
        'type': 'item.completed',
        'item': {'type': 'agent_message', 'text': text},
    }


def _text_from_content(content) -> str:
    """Join the text parts of a message's content list.

    User turns carry ``input_text`` and assistant turns ``output_text``; both
    are read, since the distinction is the CLI's, not the operator's.
    """
    if not isinstance(content, list):
        return ''
    parts = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get('type') not in ('input_text', 'output_text', 'text'):
            continue
        text = str(block.get('text', '') or '')
        if text:
            parts.append(text)
    return '\n'.join(parts).strip()
