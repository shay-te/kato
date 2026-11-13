"""What a finished per-turn CLI run said, from its JSON event stream.

A one-shot run (implement a task, fix a review comment, a read-only review)
prints the same event stream a chat turn does. The host needs three things
from it: the conversation's id (to resume it later), the agent's final reply,
and whether the run failed — and if so, the CLI's own words for why.

Works on the shared vocabulary (``turn_events``): a transport translates its
CLI's lines first and hands the result here, so every per-turn CLI is read by
this one reducer.
"""

from __future__ import annotations

import json
from typing import Any, Iterable

from agent_core_lib.agent_core_lib.data.fields import ImplementationFields
from agent_core_lib.agent_core_lib.session.turn_events import (
    TURN_EVENT_ERROR,
    TURN_EVENT_ITEM_COMPLETED,
    TURN_EVENT_TURN_FAILED,
    TURN_ITEM_AGENT_MESSAGE,
)

#: Failure events by name. The substring check in ``absorb_error`` also
#: catches any event whose type mentions an error or a failure, so a renamed
#: event still marks the run failed.
ERROR_EVENT_TYPES = frozenset({TURN_EVENT_ERROR, TURN_EVENT_TURN_FAILED})


def parse_json_event(raw_line: str) -> dict | None:
    """One JSON line as a dict, or ``None`` for a banner, a blank or noise."""
    line = str(raw_line or '').strip()
    if not line or not line.startswith('{'):
        return None
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(event, dict):  # pragma: no cover
        # Unreachable: a line starting with ``{`` that parses is an object.
        return None
    return event


def empty_turn_result() -> dict[str, Any]:
    return {
        ImplementationFields.AGENT_SESSION_ID: '',
        'is_error': False,
        'result': '',
    }


def turn_result(events: Iterable[dict]) -> dict[str, Any]:
    """Reduce a run's (translated) events to ``{agent_session_id, is_error, result}``."""
    payload = empty_turn_result()
    for event in events:
        absorb_session_id(event, payload)
        absorb_agent_message(event, payload)
        absorb_error(event, payload)
    return payload


def absorb_session_id(event: dict, payload: dict[str, Any]) -> None:
    """The first session id the run reported wins.

    ``thread_id`` is the vocabulary's key; a top-level ``session_id`` is read
    too, so a CLI that only ever names it that way still resumes.
    """
    if payload.get(ImplementationFields.AGENT_SESSION_ID):
        return
    session_id = event.get('thread_id') or event.get('session_id', '')
    if session_id:
        payload[ImplementationFields.AGENT_SESSION_ID] = str(session_id).strip()


def absorb_agent_message(event: dict, payload: dict[str, Any]) -> None:
    """The latest reply the agent completed is the run's answer."""
    if str(event.get('type', '')) != TURN_EVENT_ITEM_COMPLETED:
        return
    item = event.get('item') or {}
    if not isinstance(item, dict) or item.get('type') != TURN_ITEM_AGENT_MESSAGE:
        return
    text = item.get('text', '')
    if text:
        payload['result'] = str(text).strip()


def absorb_error(event: dict, payload: dict[str, Any]) -> None:
    """Mark the run failed on an error event and keep the CLI's explanation."""
    event_type = str(event.get('type', ''))
    is_error_event = (
        event_type in ERROR_EVENT_TYPES
        or 'error' in event_type
        or 'fail' in event_type
    )
    if not is_error_event:
        return
    payload['is_error'] = True
    error_text = extract_error_text(event)
    if error_text:
        payload['result'] = unwrap_backend_error_envelope(error_text)


def extract_error_text(event: dict) -> str:
    """The error string, from whichever of the usual shapes the CLI chose."""
    top = event.get('message')
    if isinstance(top, str) and top:
        return top
    nested = event.get('error')
    if isinstance(nested, dict):
        for key in ('message', 'error'):
            value = nested.get(key)
            if isinstance(value, str) and value:
                return value
    elif isinstance(nested, str) and nested:
        return nested
    item = event.get('item')
    if isinstance(item, dict):
        for key in ('text', 'message'):
            value = item.get(key)
            if isinstance(value, str) and value:
                return value
    return ''


def unwrap_backend_error_envelope(text: str) -> str:
    """An API error often arrives JSON-stringified inside the event's message.

    The operator wants the inner ``error.message``, not the wire envelope.
    Unwrap one level when it parses; otherwise return the text as it came.
    """
    stripped = str(text or '').strip()
    if not stripped.startswith('{'):
        return stripped
    try:
        inner = json.loads(stripped)
    except (json.JSONDecodeError, TypeError):
        return stripped
    if not isinstance(inner, dict):  # pragma: no cover
        # Unreachable: text starting with ``{`` that parses is an object.
        return stripped
    return readable_message_from_envelope(inner) or stripped


def readable_message_from_envelope(envelope: dict) -> str:
    """The operator-readable string in a parsed API error envelope, or ''."""
    nested = envelope.get('error')
    if isinstance(nested, dict):
        for key in ('message', 'error'):
            value = nested.get(key)
            if isinstance(value, str) and value:
                return value.strip()
    top = envelope.get('message')
    if isinstance(top, str) and top:
        return top.strip()
    return ''
