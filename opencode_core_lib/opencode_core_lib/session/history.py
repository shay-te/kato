"""Replay an OpenCode chat from the CLI's own session store.

A live chat's events live in its session object's memory, so a chat survived
a page reload but not a restart of the process hosting it. OpenCode keeps
every session in a SQLite database under its data directory
(``~/.local/share/opencode/opencode.db``); its schema is the CLI's own, so this
reads a session back through the CLI's documented export instead —
``opencode session export <id>`` on V2, ``opencode export <id>`` on V1 — which
prints the session as JSON::

    {"info": {...}, "messages": [
        {"info": {"role": "user", "time": {"created": <ms>}},
         "parts": [{"type": "text", "text": "…"}, …]}, …]}

Only the operator's and the agent's text is replayed — what the other
transports replay — in the live stream's shape, so the chat renders a replayed
turn through the one path it renders a live one. Anything unreadable answers
``[]``: a chat that renders empty is better than one that fails to open.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from typing import Any

from agent_core_lib.agent_core_lib.session.turn_events import agent_message, user_message
from opencode_core_lib.opencode_core_lib.helpers.cli_generation import (
    GENERATION_V2,
    cli_generation,
    generation_flags,
)

#: Cap on returned events, matching the other transports' replay. A very long
#: chat is truncated from the FRONT so the most recent exchange survives.
DEFAULT_MAX_EVENTS = 5000
#: Read by every reader of the CLI's store when the host names the binary.
BINARY_ENV_KEY = 'OPENCODE_CLI_BINARY'
_TIMEOUT_SECONDS = 30.0


def opencode_binary(env: dict | None = None) -> str:
    source = os.environ if env is None else env
    return str(source.get(BINARY_ENV_KEY, '') or '').strip() or 'opencode'


def run_store_command(arguments: list[str], *, binary: str = '') -> str:
    """stdout of a read-only ``opencode`` store command, or '' when it fails."""
    chosen = binary or opencode_binary()
    generation = cli_generation(chosen)
    try:
        proc = subprocess.run(
            [chosen, *arguments, *generation_flags(generation)],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=_TIMEOUT_SECONDS, check=False,
            env={**os.environ, 'OPENCODE_DISABLE_AUTOUPDATE': '1'},
            # Never the operator's terminal.
            stdin=subprocess.DEVNULL,
        )
    except Exception:
        return ''
    return (proc.stdout or '') if proc.returncode == 0 else ''


def export_arguments(agent_session_id: str, generation: int) -> list[str]:
    if generation >= GENERATION_V2:
        return ['session', 'export', agent_session_id]
    return ['export', agent_session_id]


def load_history_events(
    agent_session_id: str,
    *,
    binary: str = '',
    max_events: int = DEFAULT_MAX_EVENTS,
) -> list[dict[str, Any]]:
    """The chat's turns, in the live stream's wire shape; ``[]`` if unreadable."""
    session_id = str(agent_session_id or '').strip()
    if not session_id or session_id.startswith('-'):
        return []
    chosen = binary or opencode_binary()
    text = run_store_command(export_arguments(session_id, cli_generation(chosen)), binary=chosen)
    events = events_from_export(text)
    if max_events >= 0 and len(events) > max_events:
        events = events[-max_events:] if max_events else []
    return events


def events_from_export(text: str) -> list[dict[str, Any]]:
    """The user/assistant text of an exported session, oldest first."""
    try:
        data = json.loads(text) if text else None
    except ValueError:
        return []
    messages = data.get('messages') if isinstance(data, dict) else None
    events: list[dict[str, Any]] = []
    for message in messages if isinstance(messages, list) else ():
        if not isinstance(message, dict):
            continue
        info = message.get('info') if isinstance(message.get('info'), dict) else {}
        role = str(info.get('role', '') or message.get('role', '') or '')
        text = _message_text(message.get('parts'))
        if role not in ('user', 'assistant') or not text:
            continue
        event = user_message(text) if role == 'user' else agent_message(text)
        timestamp = _timestamp(info.get('time'))
        if timestamp:
            event['timestamp'] = timestamp
        events.append(event)
    return events


def _message_text(parts: Any) -> str:
    candidates = parts if isinstance(parts, list) else ()
    texts = [
        str(part.get('text', '')).strip()
        for part in candidates
        if isinstance(part, dict) and part.get('type') == 'text'
        and not part.get('synthetic') and str(part.get('text', '')).strip()
    ]
    return '\n\n'.join(texts)


def _timestamp(times: Any) -> str:
    """``time.created`` (milliseconds) as the ISO string the replay reads."""
    created = times.get('created') if isinstance(times, dict) else None
    if not isinstance(created, (int, float)) or created <= 0:
        return ''
    return datetime.fromtimestamp(created / 1000, tz=timezone.utc).isoformat()
