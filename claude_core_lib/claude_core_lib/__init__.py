"""Claude Code CLI backend.

Implements ``agent_provider_contracts.AgentProvider`` (via
``ClaudeCliClient``) so kato (and any other orchestrator) can call
into Claude through the same contract every other backend
satisfies.

Owns the Claude-specific runtime: subprocess wrapper, NDJSON
streaming session, permission-prompt-tool stdio framing,
``--resume`` session lifecycle, history replay, the Claude session
manager. The shared subprocess plumbing (subprocess spawn, NDJSON
line reader, permission protocol framing) stays inside this
package until ``codex_core_lib`` lands and there are two real
subprocess backends to triangulate the shared utility from.

Public surface:
    ClaudeCliClient        - one-shot ``claude -p`` invocations
                             (implements AgentProvider).
    StreamingClaudeSession - long-lived stream-json subprocess for
                             the planning UI's chat tab.
    ClaudeSessionManager   - per-task lifecycle management for
                             streaming sessions.
"""

from claude_core_lib.claude_core_lib.cli_client import ClaudeCliClient
from claude_core_lib.claude_core_lib.session.manager import (
    SESSION_STATUS_ACTIVE,
    SESSION_STATUS_DONE,
    SESSION_STATUS_REVIEW,
    SESSION_STATUS_TERMINATED,
    ClaudeSessionManager,
    PlanningSessionRecord,
)
from claude_core_lib.claude_core_lib.session.streaming import (
    SessionEvent,
    StreamingClaudeSession,
)
