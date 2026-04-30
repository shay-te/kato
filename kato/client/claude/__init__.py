"""Claude Code CLI backend for Kato.

Public surface:
    ClaudeCliClient       - one-shot `claude -p` invocations (current default).
    StreamingClaudeSession - long-lived stream-json subprocess for the
                             planning UI (PR 1+ work).

Both implement the :class:`kato.client.agent_client.AgentClient` contract
where applicable.
"""

from kato.client.claude.cli_client import ClaudeCliClient
from kato.client.claude.session_manager import (
    SESSION_STATUS_ACTIVE,
    SESSION_STATUS_DONE,
    SESSION_STATUS_REVIEW,
    SESSION_STATUS_TERMINATED,
    ClaudeSessionManager,
    PlanningSessionRecord,
)
from kato.client.claude.streaming_session import (
    SessionEvent,
    StreamingClaudeSession,
)
