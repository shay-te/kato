"""What an interactive agent chat must provide, whatever CLI is behind it.

The batch contract next door (``AgentProvider``) covers one-shot work: run a
task, test it, fix review comments. A CHAT is different — it outlives any one
invocation, the operator sends messages into it over time, and the UI tails its
events live.

Two execution models satisfy this, and the difference is worth stating because
it is where a naive port breaks:

* **One long-lived process** (Claude Code): a single subprocess with a
  persistent stdin/stdout protocol. Every turn goes to the same process.
* **One process per turn** (Codex): ``codex exec`` runs, emits its events, and
  exits. Continuity comes from resuming a session id on the next turn.

So ``is_alive`` cannot mean "a subprocess is running" — under the per-turn
model that is false between every turn while the chat is perfectly usable. It
means **"this chat can still take a message"**, which is true for both models
and is what every caller actually wants to know.

Nothing here is an ABC. The consumers use ``getattr`` with fallbacks, so a
transport may implement the optional half incrementally; this module is the
written-down contract those fallbacks assume, and the place to look before
adding a new backend.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class AgentSessionEvent(Protocol):
    """One event from the agent, as the UI consumes it."""

    @property
    def event_type(self) -> str:
        """Transport-specific event name, surfaced to the UI as-is."""

    @property
    def is_terminal(self) -> bool:
        """True for the event that ends a turn.

        The UI clears its in-flight indicator on this, so a transport that
        never marks one leaves the chat looking permanently busy.
        """

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable form for the SSE stream and the event log."""


@runtime_checkable
class AgentSession(Protocol):
    """An interactive chat with an agent, for one task.

    REQUIRED — every consumer calls these directly:
    """

    @property
    def task_id(self) -> str:
        """The task this chat belongs to."""

    @property
    def agent_session_id(self) -> str:
        """The CLI's own id for this conversation, or ``''`` before one exists.

        Persisted so a chat survives a host restart: the next turn resumes
        this id rather than starting a conversation with no memory.
        """

    @property
    def cwd(self) -> str:
        """Working directory the agent runs in."""

    @property
    def is_alive(self) -> bool:
        """Can this chat still take a message?

        NOT "is a subprocess running" — see the module docstring. Under the
        per-turn model this stays True between turns.
        """

    def start(self, initial_prompt: str = '') -> None:
        """Bring the chat up, optionally sending a first message."""

    def send_user_message(self, text: str, **kwargs: Any) -> None:
        """Send one operator message. Returns once the turn is UNDERWAY —
        never blocks until it completes."""

    def recent_events(self, limit: int | None = None) -> list[AgentSessionEvent]:
        """Every event so far, oldest first."""

    def events_after(self, start_index: int) -> tuple[list[AgentSessionEvent], int]:
        """Events at or after ``start_index``, plus the new high-water mark.

        The SSE tail calls this once per wakeup; it must be O(new), not a
        copy of the whole log.
        """

    def poll_event(self, timeout: float = 0.0) -> AgentSessionEvent | None:
        """Next event, optionally waiting up to ``timeout``."""

    @property
    def terminal_event(self) -> AgentSessionEvent | None:
        """The event that ended the last turn, if one has."""

    def stderr_snapshot(self) -> list[str]:
        """Recent stderr lines — what the operator sees when a spawn fails."""

    def terminate(self, grace_seconds: float = 1.0) -> None:
        """Shut the chat down. Must be safe to call twice, and must not block
        indefinitely on an unresponsive CLI."""


# Optional surface. Consumers read these with ``getattr(session, name, default)``
# and degrade gracefully, so a transport can add them as it grows:
#
#   is_working              -> bool   True mid-turn; drives the UI's busy dot.
#   context_usage()         -> dict   Token usage for the cost indicator.
#   allowed_additional_dirs()-> list  Sandbox roots, for the restart hint.
#   pending_control_requests()-> list Permission asks awaiting an operator.
#   send_permission_response(...)     Answer one of those asks.
OPTIONAL_SESSION_ATTRIBUTES = (
    'is_working',
    'context_usage',
    'allowed_additional_dirs',
    'pending_control_requests',
    'send_permission_response',
)
