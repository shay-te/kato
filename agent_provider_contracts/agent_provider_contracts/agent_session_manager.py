"""What a per-task chat manager must provide, whatever CLI is behind it.

A host holds ONE session manager per agent backend and routes each task's
live-chat calls to the manager of the backend recorded on that task's chat.
These four methods are the part every manager answers; a manager may own more
(the persistent-process one also keeps the chat records for every backend).
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from agent_provider_contracts.agent_provider_contracts.agent_session import (
    AgentSession,
)


@runtime_checkable
class AgentSessionManager(Protocol):
    """Start, find and tear down the live chat of each task."""

    def get_session(self, task_id: str) -> AgentSession | None:
        """The task's live chat, or ``None`` when it has none."""

    def start_session(
        self,
        *,
        task_id: str,
        cwd: str = '',
        binary: str = '',
        model: str = '',
        task_summary: str = '',
        expected_branch: str = '',
        additional_dirs: Any = (),
        initial_prompt: str = '',
        agent_session_id: str = '',
        **kwargs: Any,
    ) -> AgentSession:
        """The task's live chat, starting one (or resuming
        ``agent_session_id``) when it has none."""

    def terminate_session(self, task_id: str, **kwargs: Any) -> None:
        """End the task's chat. Safe when it has none."""

    def shutdown(self) -> None:
        """End every chat this manager holds."""
