"""The one event vocabulary every per-turn CLI chat speaks to its host.

A per-turn CLI (Codex, Grok, OpenCode) runs one process for each operator
message and prints a JSON event stream while it works. Each CLI names its
events differently, but a host renders exactly one shape: the UI draws a turn
from the names below and from nothing else.

So every per-turn transport TRANSLATES its CLI's own events into this
vocabulary (``session/wire_protocol.py`` in each transport) before anything
outside the transport sees them. Codex's own names are the vocabulary, so its
translation is the identity; Grok and OpenCode map onto it. A transport that
passed its raw events through instead would show the operator event names
("step_finish", "message.part.updated") as grey chips while the reply sat
unread inside them.

The builders below are the only way a transport should make one of these
events: the shape of "the agent said X" lives here once.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

#: First event of a conversation; ``thread_id`` is the CLI's session id, which
#: the host persists and resumes on every later turn.
TURN_EVENT_SESSION_STARTED = 'thread.started'
TURN_EVENT_TURN_STARTED = 'turn.started'
TURN_EVENT_ITEM_STARTED = 'item.started'
TURN_EVENT_ITEM_UPDATED = 'item.updated'
#: One produced artefact (a reply, a command run, a file edit) under ``item``.
TURN_EVENT_ITEM_COMPLETED = 'item.completed'
#: The turn finished. ``usage`` carries token counts when the CLI reports them.
TURN_EVENT_TURN_COMPLETED = 'turn.completed'
#: The CLI reached the model and the model (or its API) refused the turn.
TURN_EVENT_TURN_FAILED = 'turn.failed'
#: Synthesised by the transport, never by a CLI: the process ended without a
#: terminal event (a crash, a kill, a non-zero exit).
TURN_EVENT_TURN_ABORTED = 'turn.aborted'
#: A CLI-reported error; ``message`` is the text to show.
TURN_EVENT_ERROR = 'error'
#: The operator's own message, in the shape the chat already draws.
TURN_EVENT_USER = 'user'

TURN_ITEM_AGENT_MESSAGE = 'agent_message'
TURN_ITEM_REASONING = 'reasoning'
TURN_ITEM_COMMAND_EXECUTION = 'command_execution'
TURN_ITEM_FILE_CHANGE = 'file_change'
TURN_ITEM_MCP_TOOL_CALL = 'mcp_tool_call'
TURN_ITEM_WEB_SEARCH = 'web_search'
TURN_ITEM_TODO_LIST = 'todo_list'
TURN_ITEM_ERROR = 'error'

#: Events that END a turn: the UI's in-flight indicator clears on any of them.
TERMINAL_TURN_EVENTS = frozenset({
    TURN_EVENT_TURN_COMPLETED,
    TURN_EVENT_TURN_FAILED,
    TURN_EVENT_TURN_ABORTED,
})


@dataclass
class TurnEvent(object):
    """One event in a per-turn chat's log.

    The same envelope as the persistent-process transport's event: the host
    serialises both through ``to_dict()``, which is what lets one UI tail any
    backend.
    """

    raw: dict[str, Any] = field(default_factory=dict)
    received_at_epoch: float = field(default_factory=time.time)

    @property
    def event_type(self) -> str:
        return str(self.raw.get('type', '') or '')

    @property
    def subtype(self) -> str:
        return str(self.raw.get('subtype', '') or '')

    @property
    def is_terminal(self) -> bool:
        return self.event_type in TERMINAL_TURN_EVENTS

    def to_dict(self) -> dict[str, Any]:
        return {'received_at_epoch': self.received_at_epoch, 'raw': self.raw}


# ----- builders: the only way a translator makes an event --------------------


def session_started(session_id: str) -> dict[str, Any]:
    return {'type': TURN_EVENT_SESSION_STARTED, 'thread_id': str(session_id or '')}


def turn_started() -> dict[str, Any]:
    return {'type': TURN_EVENT_TURN_STARTED}


def user_message(text: str) -> dict[str, Any]:
    return {
        'type': TURN_EVENT_USER,
        'message': {'content': [{'type': 'text', 'text': str(text or '')}]},
    }


def completed_item(item_type: str, **fields: Any) -> dict[str, Any]:
    return {'type': TURN_EVENT_ITEM_COMPLETED, 'item': {'type': item_type, **fields}}


def agent_message(text: str) -> dict[str, Any]:
    return completed_item(TURN_ITEM_AGENT_MESSAGE, text=str(text or ''))


def reasoning(text: str) -> dict[str, Any]:
    return completed_item(TURN_ITEM_REASONING, text=str(text or ''))


def command_execution(
    command: str, *, output: str = '', exit_code: int | None = None,
) -> dict[str, Any]:
    fields: dict[str, Any] = {'command': str(command or ''), 'aggregated_output': str(output or '')}
    if exit_code is not None:
        fields['exit_code'] = exit_code
    return completed_item(TURN_ITEM_COMMAND_EXECUTION, **fields)


def file_change(paths: list[str] | tuple[str, ...]) -> dict[str, Any]:
    changes = [{'path': str(path)} for path in paths if str(path or '').strip()]
    return completed_item(TURN_ITEM_FILE_CHANGE, changes=changes)


def tool_call(tool: str, *, server: str = '') -> dict[str, Any]:
    return completed_item(TURN_ITEM_MCP_TOOL_CALL, tool=str(tool or ''), server=str(server or ''))


def web_search(query: str) -> dict[str, Any]:
    return completed_item(TURN_ITEM_WEB_SEARCH, query=str(query or ''))


def turn_completed(usage: dict[str, Any] | None = None) -> dict[str, Any]:
    event: dict[str, Any] = {'type': TURN_EVENT_TURN_COMPLETED}
    if usage:
        event['usage'] = dict(usage)
    return event


def turn_failed(message: str) -> dict[str, Any]:
    return {'type': TURN_EVENT_TURN_FAILED, 'error': {'message': str(message or '')}}


def error_event(message: str) -> dict[str, Any]:
    return {'type': TURN_EVENT_ERROR, 'message': str(message or '')}
