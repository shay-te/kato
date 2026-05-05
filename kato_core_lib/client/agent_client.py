"""Shared interface for the AI agent backend that powers Kato.

A backend (Claude CLI, OpenHands, future Gemini, ...) is a thing that takes
a Kato task and produces a result describing what changed. Each one lives
in its own package (``kato.client.claude``, ``kato.client.openhands``)
and implements this Protocol so :class:`KatoCoreLib`, :class:`ImplementationService`,
:class:`TestingService`, and :class:`StartupDependencyValidator` can stay
backend-agnostic.

To add a new backend:

1. Create ``kato/client/<backend>/`` with at least one client class that
   implements every method in :class:`AgentClient` below.
2. Wire it up in :func:`KatoCoreLib._build_agent_client` next to the
   existing ``claude`` / ``openhands`` branches.
3. Add it to ``resolved_agent_backend()`` so ``KATO_AGENT_BACKEND=<name>``
   selects it.

The contract is intentionally narrow — anything bigger than these methods
should stay inside the backend package. Shared prompt-building helpers
live in :mod:`kato.helpers.agent_prompt_utils`; result shaping in
:mod:`kato.helpers.kato_result_utils`.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from kato_core_lib.data_layers.data.review_comment import ReviewComment
from kato_core_lib.data_layers.data.task import Task
from kato_core_lib.helpers.task_context_utils import PreparedTaskContext


@runtime_checkable
class AgentClient(Protocol):
    """The minimum surface a backend must expose to plug into Kato.

    All methods raise :class:`RuntimeError` (or a subclass) on failure.
    Connection-level retries are the backend's own concern; the
    orchestration layer treats one call as one attempt.
    """

    #: Number of times the orchestration layer should retry connection-level
    #: errors when calling this client. Backends typically derive this from
    #: ``KATO_EXTERNAL_API_MAX_RETRIES``.
    max_retries: int

    def validate_connection(self) -> None:
        """Run the startup health check.

        Called once at startup by :class:`StartupDependencyValidator`. Should
        confirm the backend is reachable and authenticated, and (if
        configured) run a tiny smoke prompt to verify model access. Raises
        on any failure with a human-readable error message.
        """

    def validate_model_access(self) -> None:
        """Re-run model-access validation just-in-time before a task.

        Cheap if it has already run successfully this process. Called by
        :class:`TaskModelAccessValidator` at the top of every task so a
        revoked credential fails the task fast instead of mid-run.
        """

    def delete_conversation(self, conversation_id: str) -> None:
        """Free any backend-side resources tied to a single conversation.

        For OpenHands this stops the agent-server container; for Claude CLI
        it's a no-op (sessions are local files). Best-effort: backends that
        cannot find the conversation should log and return, not raise.
        """

    def stop_all_conversations(self) -> None:
        """Free every backend-side conversation. Called on process shutdown."""

    def implement_task(
        self,
        task: Task,
        session_id: str = '',
        prepared_task: PreparedTaskContext | None = None,
    ) -> dict[str, str | bool]:
        """Run the implementation pass for a task.

        Must return a dict shaped by :func:`build_openhands_result` —
        ``{success: bool, summary: str, [commit_message], [message],
        [session_id], [branch_name]}``. Raises on transport-level failures.
        """

    def test_task(
        self,
        task: Task,
        prepared_task: PreparedTaskContext | None = None,
    ) -> dict[str, str | bool]:
        """Run the optional testing-validation pass for a task.

        Same return shape as :meth:`implement_task`. Skipped entirely when
        ``OPENHANDS_SKIP_TESTING=true``.
        """

    def fix_review_comment(
        self,
        comment: ReviewComment,
        branch_name: str,
        session_id: str = '',
        task_id: str = '',
        task_summary: str = '',
    ) -> dict[str, str | bool]:
        """Address a single PR review comment on the existing task branch.

        ``session_id`` lets a backend resume the same conversation that
        produced the implementation, for context continuity. Same return
        shape as :meth:`implement_task`.
        """

    def fix_review_comments(
        self,
        comments: list[ReviewComment],
        branch_name: str,
        session_id: str = '',
        task_id: str = '',
        task_summary: str = '',
        mode: str = 'fix',
    ) -> dict[str, str | bool]:
        """Address multiple PR review comments on the existing task branch.

        ``comments`` must all belong to the same pull request. The
        backend addresses every comment in a single agent spawn,
        producing one commit and one push for the whole batch.

        Same return shape as :meth:`fix_review_comment`. The
        single-comment case is equivalent to calling
        ``fix_review_comment`` directly — backends use the legacy
        prompt for ``len(comments) == 1`` to avoid regressions.
        """
