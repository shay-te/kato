from __future__ import annotations

import threading

from kato_core_lib.data_layers.data.task import Task
from kato_core_lib.data_layers.service.agent_client_service import _AgentClientService
from kato_core_lib.helpers.task_context_utils import PreparedTaskContext


class ImplementationService(_AgentClientService):
    """Wrap the active agent client for implementation and review-comment fixing."""

    def delete_conversation(self, conversation_id: str) -> None:
        self._client.delete_conversation(conversation_id)

    @property
    def supports_investigation(self) -> bool:
        """Can the active backend run a fresh, read-only, one-off turn?

        Claude and Codex can; OpenHands cannot. Features that need an
        independent reader (an automated code review) check this up front and
        refuse with a reason instead of failing half-way.
        """
        return callable(getattr(self._client, 'investigate', None))

    def investigate(
        self,
        prompt: str,
        *,
        cwd: str = '',
        additional_dirs: list[str] | None = None,
        sandbox_root: str = '',
        task_id: str = '',
        log_label: str = '',
        cancel_event: threading.Event | None = None,
        timeout_seconds: int = 0,
        model: str = '',
    ) -> str:
        """One fresh read-only turn on the active backend; returns its text.

        See the client's ``investigate``: no ``--resume``, every write path
        denied, nothing persisted, stoppable through ``cancel_event``.
        """
        if not self.supports_investigation:
            raise RuntimeError(
                'the active agent backend cannot run a read-only investigation',
            )
        return self._client.investigate(
            prompt,
            cwd=cwd,
            additional_dirs=additional_dirs,
            sandbox_root=sandbox_root,
            task_id=task_id,
            log_label=log_label,
            cancel_event=cancel_event,
            timeout_seconds=timeout_seconds,
            model=model,
        )

    @property
    def investigation_model(self) -> str:
        """The model a read-only turn runs on unless the caller picks one."""
        return str(getattr(self._client, 'model', '') or '')

    def implement_task(
        self,
        task: Task,
        agent_session_id: str = '',
        prepared_task: PreparedTaskContext | None = None,
    ) -> dict[str, str | bool]:
        self.logger.info('delegating implementation for task %s', task.id)
        return self._client.implement_task(
            task,
            agent_session_id,
            prepared_task=prepared_task,
        )

    def fix_review_comment(
        self,
        comment,
        branch_name: str,
        agent_session_id: str = '',
        task_id: str = '',
        task_summary: str = '',
        additional_dirs=None,
    ) -> dict[str, str | bool]:
        return self._client.fix_review_comment(
            comment,
            branch_name,
            agent_session_id,
            task_id=task_id,
            task_summary=task_summary,
            additional_dirs=additional_dirs,
        )

    def fix_review_comments(
        self,
        comments,
        branch_name: str,
        agent_session_id: str = '',
        task_id: str = '',
        task_summary: str = '',
        mode: str = 'fix',
        additional_dirs=None,
    ) -> dict[str, str | bool]:
        """Address every comment in ``comments`` via the agent client.

        Newer clients (Claude, OpenHands) implement ``fix_review_comments``
        natively and address the whole batch in one agent spawn — that's
        the efficiency win. Older clients (or test stubs) that only
        expose ``fix_review_comment`` get auto-fanned-out: one client call
        per comment, results merged. Behaviour-preserving back-compat for
        anyone who wrote a custom agent client against the old API.

        ``mode='answer'`` routes the agent through the question-answering
        prompt — service caller skips the push step in that case.
        """
        if hasattr(self._client, 'fix_review_comments'):
            return self._client.fix_review_comments(
                comments,
                branch_name,
                agent_session_id=agent_session_id,
                task_id=task_id,
                task_summary=task_summary,
                mode=mode,
                additional_dirs=additional_dirs,
            )
        # Fallback: iterate. Loses the batching efficiency, but
        # preserves correctness — every comment still gets addressed.
        # Older clients without ``mode`` support fall through to fix
        # mode silently; the service-level skip-push branch still
        # applies, so worst case the agent makes an unnecessary
        # commit that nobody pushes.
        last_result: dict[str, str | bool] = {}
        for comment in comments:
            try:
                last_result = self._client.fix_review_comment(
                    comment,
                    branch_name,
                    agent_session_id,
                    task_id=task_id,
                    task_summary=task_summary,
                    additional_dirs=additional_dirs,
                )
            except TypeError:
                # Older/custom clients written before ``additional_dirs``
                # existed don't accept the kwarg — degrade to single-repo
                # scope rather than crash the whole batch.
                last_result = self._client.fix_review_comment(
                    comment,
                    branch_name,
                    agent_session_id,
                    task_id=task_id,
                    task_summary=task_summary,
                )
        return last_result
