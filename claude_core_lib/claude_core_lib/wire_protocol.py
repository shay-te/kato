"""Wire-protocol constants shared by kato and the planning UI.

Kato consumes Claude CLI's stream-json output (`claude -p
--output-format stream-json`) and re-emits it to browsers as
Server-Sent Events. The strings on both ends of those wires are
duplicated naturally — a typo on one side silently breaks reconnect
or the permission modal — so they live here as Python constants and
mirror the JS `constants/claudeEvent.js` + `useSessionStream` event
types one-for-one. Keep both sides in sync when anything changes.
"""

from __future__ import annotations


# ----- Claude CLI stream-json event types -----
#
# Mirrors webserver/ui/src/constants/claudeEvent.js (CLAUDE_EVENT.*).

CLAUDE_EVENT_ASSISTANT = 'assistant'
CLAUDE_EVENT_USER = 'user'
CLAUDE_EVENT_SYSTEM = 'system'
CLAUDE_EVENT_RESULT = 'result'
CLAUDE_EVENT_STREAM_EVENT = 'stream_event'
CLAUDE_EVENT_PERMISSION_REQUEST = 'permission_request'
CLAUDE_EVENT_CONTROL_REQUEST = 'control_request'
CLAUDE_EVENT_CONTROL_RESPONSE = 'control_response'

# Synthetic events kato injects into the event log so reconnecting
# browsers can clear stale UI state from the backlog.
CLAUDE_EVENT_PERMISSION_RESPONSE = 'permission_response'

CLAUDE_SYSTEM_SUBTYPE_INIT = 'init'

PERMISSION_REQUEST_EVENT_TYPES = frozenset({
    CLAUDE_EVENT_PERMISSION_REQUEST,
    CLAUDE_EVENT_CONTROL_REQUEST,
})


# ----- kato → browser SSE event names -----
#
# Mirrors the addEventListener('<name>') calls in
# webserver/ui/src/hooks/useSessionStream.js (per-task chat) and
# webserver/ui/src/hooks/useStatusFeed.js (global kato status feed).

SSE_EVENT_SESSION_EVENT = 'session_event'
SSE_EVENT_SESSION_HISTORY_EVENT = 'session_history_event'
SSE_EVENT_SESSION_IDLE = 'session_idle'
SSE_EVENT_SESSION_MISSING = 'session_missing'
SSE_EVENT_SESSION_CLOSED = 'session_closed'

SSE_EVENT_STATUS_ENTRY = 'status_entry'
SSE_EVENT_STATUS_DISABLED = 'status_disabled'
