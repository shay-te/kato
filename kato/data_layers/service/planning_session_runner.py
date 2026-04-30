"""Bridge: take a Kato task, run it as a live planning session, return a one-shot-shaped result.

When ``KATO_CLAUDE_BYPASS_PERMISSIONS=false`` and the task carries the
``kato:wait-planning`` tag, the orchestrator uses this helper instead of
the one-shot :class:`ClaudeCliClient.implement_task` path. The helper
spawns a long-lived :class:`StreamingClaudeSession` via the shared
:class:`ClaudeSessionManager`, blocks until the agent emits its terminal
``result`` event, and shapes that into the same ``dict`` the rest of the
orchestration already understands. The browser tab connected to the
session sees the same events stream past in real time and can chat /
approve permissions while the agent works.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from kato.client.claude.session_manager import (
    SESSION_STATUS_ACTIVE,
    SESSION_STATUS_REVIEW,
    SESSION_STATUS_TERMINATED,
    ClaudeSessionManager,
)
from kato.data_layers.data.fields import ImplementationFields
from kato.data_layers.data.review_comment import ReviewComment
from kato.data_layers.data.task import Task
from kato.helpers import agent_prompt_utils
from kato.helpers.kato_result_utils import build_openhands_result
from kato.helpers.logging_utils import configure_logger
from kato.helpers.task_context_utils import PreparedTaskContext
from kato.helpers.text_utils import normalized_text


def _coerce_optional_int(value) -> int | None:
    """Parse a positive int from omegaconf-style values; None on anything else."""
    if value in (None, ''):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


@dataclass
class StreamingSessionDefaults(object):
    """The Claude-config settings the manager needs to spawn a session.

    Stored as a plain dataclass so :class:`KatoCoreLib` can build it from
    its existing config block without coupling the orchestrator to omegaconf.
    """

    binary: str = 'claude'
    model: str = ''
    permission_mode: str = 'acceptEdits'
    permission_prompt_tool: str = ''
    allowed_tools: str = ''
    disallowed_tools: str = ''
    max_turns: int | None = None
    effort: str = ''


class PlanningSessionRunner(object):
    """Run one task end-to-end via a streaming Claude session.

    Drop-in replacement for the one-shot ``implement_task`` call when the
    task is tagged ``kato:wait-planning``. The returned dict matches what
    :func:`build_openhands_result` would produce, so the existing publish
    flow (commit, push, PR, review-state transition) works unchanged.
    """

    DEFAULT_MAX_WAIT_SECONDS = 60 * 60 * 4   # generous: humans plan slowly
    DEFAULT_DRAIN_TIMEOUT_SECONDS = 0.25

    @classmethod
    def from_config(
        cls,
        open_cfg,
        agent_backend: str,
        session_manager: ClaudeSessionManager | None,
    ) -> 'PlanningSessionRunner | None':
        """Build the runner (or return None) from the kato config block.

        Returns None when the active backend has no streaming model (e.g.
        OpenHands) or when the session manager wasn't created — the rest of
        the orchestration is wired to fall back to the one-shot path in
        those cases.
        """
        if str(agent_backend or '').strip().lower() != 'claude':
            return None
        if session_manager is None:
            return None
        claude_cfg = getattr(open_cfg, 'claude', None)
        if claude_cfg is None:
            return None
        defaults = cls._build_defaults(claude_cfg)
        return cls(session_manager=session_manager, defaults=defaults)

    @staticmethod
    def _build_defaults(claude_cfg) -> 'StreamingSessionDefaults':
        bypass = bool(getattr(claude_cfg, 'bypass_permissions', False))
        return StreamingSessionDefaults(
            binary=str(getattr(claude_cfg, 'binary', '') or 'claude'),
            model=str(getattr(claude_cfg, 'model', '') or ''),
            permission_mode='bypassPermissions' if bypass else 'acceptEdits',
            allowed_tools=str(getattr(claude_cfg, 'allowed_tools', '') or ''),
            disallowed_tools=str(getattr(claude_cfg, 'disallowed_tools', '') or ''),
            max_turns=_coerce_optional_int(getattr(claude_cfg, 'max_turns', None)),
            effort=str(getattr(claude_cfg, 'effort', '') or ''),
        )

    def __init__(
        self,
        session_manager: ClaudeSessionManager,
        defaults: StreamingSessionDefaults,
        *,
        max_wait_seconds: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._session_manager = session_manager
        self._defaults = defaults
        self._max_wait_seconds = (
            max_wait_seconds
            if max_wait_seconds is not None
            else self.DEFAULT_MAX_WAIT_SECONDS
        )
        self._clock = clock
        self.logger = configure_logger(self.__class__.__name__)

    def implement_task(
        self,
        task: Task,
        prepared_task: PreparedTaskContext | None = None,
    ) -> dict[str, str | bool]:
        branch_name = agent_prompt_utils.task_branch_name(task, prepared_task)
        return self._run_to_terminal(
            task_id=str(task.id),
            task_summary=normalized_text(task.summary),
            cwd=self._working_directory(prepared_task),
            initial_prompt=self._build_implementation_prompt(task, prepared_task),
            branch_name=branch_name,
            default_commit_message=f'Implement {task.id}',
            log_label='planning session',
        )

    def fix_review_comment(
        self,
        comment: ReviewComment,
        branch_name: str,
        *,
        task_id: str,
        task_summary: str = '',
        repository_local_path: str = '',
    ) -> dict[str, str | bool]:
        """Run a review-comment fix as a streaming session bound to ``task_id``.

        Each review-fix gets a fresh subprocess so the runner has a clean
        ``terminal_event`` to wait on; the persisted Claude session_id
        carries conversation context across the restart. The browser tab
        stays bound to ``task_id``, so the user sees the new turn stream
        in next to the original implementation history.
        """
        normalized_task_id = str(task_id or '').strip()
        if not normalized_task_id:
            raise ValueError('task_id is required to fix a review comment')
        # Tear down any prior subprocess so we observe a fresh terminal
        # event for this turn. start_session below resumes the persisted
        # Claude session_id via --resume, so context survives.
        if self._session_manager.get_session(normalized_task_id) is not None:
            self._session_manager.terminate_session(normalized_task_id)
        return self._run_to_terminal(
            task_id=normalized_task_id,
            task_summary=normalized_text(task_summary),
            cwd=normalized_text(repository_local_path),
            initial_prompt=self._build_review_prompt(comment, branch_name),
            branch_name=normalized_text(branch_name),
            default_commit_message='Address review comments',
            log_label='review-fix session',
        )

    def _run_to_terminal(
        self,
        *,
        task_id: str,
        task_summary: str,
        cwd: str,
        initial_prompt: str,
        branch_name: str,
        default_commit_message: str,
        log_label: str,
    ) -> dict[str, str | bool]:
        """Spawn the streaming session, block until terminal, shape the result.

        Shared by every entrypoint that runs the agent end-to-end. The
        per-call differences (which prompt to send, which commit message
        to default to, which log label to use, ...) are passed in.
        """
        self.logger.info(
            'starting %s for task %s (cwd=%s)', log_label, task_id, cwd or '?',
        )
        session = self._start_session(
            task_id=task_id,
            task_summary=task_summary,
            initial_prompt=initial_prompt,
            cwd=cwd,
            branch_name=branch_name,
        )
        terminal = self._wait_for_terminal_event(session, task_id=task_id)
        if terminal is None:
            self._session_manager.update_status(task_id, SESSION_STATUS_TERMINATED)
            raise RuntimeError(
                f'{log_label} for task {task_id} ended without a result event'
            )
        result_text = self._raise_if_terminal_failed(
            terminal, task_id=task_id, log_label=log_label,
        )

        # Tab back to blue while the orchestrator publishes / waits for review.
        self._session_manager.update_status(task_id, SESSION_STATUS_REVIEW)
        return build_openhands_result(
            {
                ImplementationFields.SUCCESS: True,
                'summary': result_text,
                ImplementationFields.MESSAGE: result_text,
                ImplementationFields.SESSION_ID: session.claude_session_id,
            },
            branch_name=branch_name,
            default_commit_message=default_commit_message,
            default_success=True,
        )

    def _start_session(
        self,
        *,
        task_id: str,
        task_summary: str,
        initial_prompt: str,
        cwd: str,
        branch_name: str,
    ):
        return self._session_manager.start_session(
            task_id=task_id,
            task_summary=task_summary,
            initial_prompt=initial_prompt,
            cwd=cwd,
            binary=self._defaults.binary,
            model=self._defaults.model,
            permission_mode=self._defaults.permission_mode,
            permission_prompt_tool=self._defaults.permission_prompt_tool,
            allowed_tools=self._defaults.allowed_tools,
            disallowed_tools=self._defaults.disallowed_tools,
            max_turns=self._defaults.max_turns,
            effort=self._defaults.effort,
            expected_branch=branch_name,
        )

    def _raise_if_terminal_failed(
        self,
        terminal,
        *,
        task_id: str,
        log_label: str,
    ) -> str:
        """Translate a terminal ``result`` event into success text or an error."""
        result_payload = terminal.raw or {}
        result_text = normalized_text(result_payload.get('result', ''))
        if bool(result_payload.get('is_error', False)):
            self._session_manager.update_status(task_id, SESSION_STATUS_TERMINATED)
            detail = result_text or f'{log_label} reported an error'
            raise RuntimeError(f'{log_label} failed: {detail}')
        return result_text

    @staticmethod
    def _build_review_prompt(comment: ReviewComment, branch_name: str) -> str:
        # Reuse the one-shot client's prompt so streaming and one-shot
        # paths show identical guardrails.
        from kato.client.claude.cli_client import ClaudeCliClient

        return ClaudeCliClient._build_review_prompt(comment, branch_name)

    def _wait_for_terminal_event(self, session, *, task_id: str):
        deadline = self._clock() + max(0.0, float(self._max_wait_seconds))
        terminal = None
        while True:
            event = session.poll_event(timeout=self.DEFAULT_DRAIN_TIMEOUT_SECONDS)
            if event is not None:
                if event.is_terminal:
                    terminal = event
                    break
                continue
            if not session.is_alive:
                terminal = session.terminal_event
                break
            if self._clock() >= deadline:
                self.logger.warning(
                    'planning session for task %s exceeded max wait of %.0fs',
                    task_id,
                    self._max_wait_seconds,
                )
                break
        return terminal

    @staticmethod
    def _working_directory(prepared_task: PreparedTaskContext | None) -> str:
        if prepared_task is None:
            return ''
        repositories = list(prepared_task.repositories or [])
        if not repositories:
            return ''
        return normalized_text(getattr(repositories[0], 'local_path', '') or '')

    @staticmethod
    def _build_implementation_prompt(
        task: Task,
        prepared_task: PreparedTaskContext | None,
    ) -> str:
        # Reuse the same prompt the one-shot ClaudeCliClient builds so the
        # planning agent sees identical guardrails and instructions.
        from kato.client.claude.cli_client import ClaudeCliClient

        builder = ClaudeCliClient(binary='unused-builder-only')
        return builder._build_implementation_prompt(task, prepared_task)
