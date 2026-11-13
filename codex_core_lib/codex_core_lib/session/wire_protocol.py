"""Codex CLI wire protocol: the events ``codex exec --json`` prints.

Codex's own event names ARE the shared per-turn vocabulary
(``agent_core_lib.session.turn_events``) — the vocabulary was taken from
Codex — so translating a Codex event is the identity. The constants below
name the events this transport relies on, in the CLI's own terms.

Observed on codex-cli 0.132.0::

    {"type": "thread.started", "thread_id": "<uuid>"}
    {"type": "turn.started"}
    {"type": "item.completed", "item": {"type": "agent_message", "text": "..."}}
    {"type": "turn.completed", "usage": {...}}
    {"type": "turn.failed", "error": {"message": "..."}}
    {"type": "error", "message": "<JSON-encoded API error envelope>"}
"""

from __future__ import annotations

from typing import Any

from agent_core_lib.agent_core_lib.session.turn_events import (
    TURN_EVENT_SESSION_STARTED,
    TURN_EVENT_TURN_ABORTED,
    TURN_EVENT_TURN_COMPLETED,
    TURN_EVENT_TURN_FAILED,
)

#: Emitted once per turn when the process starts; carries the thread id that
#: becomes the chat's ``agent_session_id``.
CODEX_EVENT_THREAD_STARTED = TURN_EVENT_SESSION_STARTED
#: Emitted once at the end of a turn — the terminal event the UI clears on.
CODEX_EVENT_TURN_COMPLETED = TURN_EVENT_TURN_COMPLETED
#: Synthesised by the session (not by the CLI) when a turn ends without a
#: terminal event — a crash, a kill, a non-zero exit.
CODEX_EVENT_TURN_ABORTED = TURN_EVENT_TURN_ABORTED
#: Emitted BY THE CLI when the model (or the API) refused the turn — a bad
#: model name, an auth problem, a rate limit. Terminal: the process exits
#: straight after, and its ``error.message`` is the one line that says what to
#: change.
CODEX_EVENT_TURN_FAILED = TURN_EVENT_TURN_FAILED


def translate_event(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """A Codex event in the shared vocabulary: itself."""
    return [raw]
