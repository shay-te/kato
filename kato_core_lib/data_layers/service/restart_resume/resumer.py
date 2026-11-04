"""After a restart: tell each agent that was working to carry on.

The message goes through the chat channel every kato-authored message uses (the
review loop's, the plan split's): one per-task lock, and a respawn with
``--resume`` so the chat continues its own conversation. A task that was deleted
or finished since, or has no conversation to continue, is left alone — and so is
one already running again (the scan, a queued comment).
"""

from __future__ import annotations

from workspace_core_lib.workspace_core_lib.data_layers.data.workspace_record import (
    WORKSPACE_STATUS_DONE,
    WORKSPACE_STATUS_TERMINATED,
)

from kato_core_lib.helpers.forgotten_tasks_store import is_forgotten
from kato_core_lib.helpers.late_binding import provider_for
from kato_core_lib.helpers.logging_utils import configure_logger

#: Labelled as kato's own, like every message kato writes into a chat.
RESUME_PROMPT = (
    'Kato — resumed after a restart\n\n'
    'Kato was stopped while you were working on this task, and has started again. '
    'Continue the work from where you stopped. A command that was running when kato '
    'stopped did not finish: run it again if you still need its result.'
)

RESUMED = 'resumed'


class AgentResumer(object):
    def __init__(
        self, *, channel, session_manager, workspace_manager, forgotten=is_forgotten, logger=None,
    ) -> None:
        self._channel = channel
        self._get_session_manager = provider_for(session_manager)
        self._get_workspace_manager = provider_for(workspace_manager)
        self._forgotten = forgotten
        self.logger = logger or configure_logger('AgentResumer')

    def resume(self, task_ids, *, on_done=lambda task_id: None) -> dict[str, str]:
        """``task id -> RESUMED`` or why it was not. ``on_done(task_id)`` after
        each, resumed or not."""
        outcomes: dict[str, str] = {}
        for task_id in task_ids:
            try:
                outcomes[task_id] = self._resume_one(task_id)
            except Exception as exc:
                outcomes[task_id] = f'it could not be resumed: {exc}'
            on_done(task_id)
            if outcomes[task_id] == RESUMED:
                self.logger.info('task %s: its agent was working when kato stopped; told it to continue', task_id)
            else:
                self.logger.info('task %s was working when kato stopped, but %s', task_id, outcomes[task_id])
        return outcomes

    def _resume_one(self, task_id: str) -> str:
        reason = self._why_not(task_id)
        if reason:
            return reason
        with self._channel.dispatch_lock(task_id):
            readiness = self._channel.readiness(task_id)
            if readiness.reason:
                return f'it is not resumed: {readiness.reason}'
            if not self._channel.deliver(task_id, RESUME_PROMPT, force_respawn=False):
                return 'its chat could not be started'
        return RESUMED

    def _why_not(self, task_id: str) -> str:
        if self._forgotten(task_id):
            return 'it was deleted since'
        workspaces = self._get_workspace_manager()
        record = workspaces.get(task_id) if workspaces is not None else None
        if record is None:
            return 'its workspace is gone'
        status = str(getattr(record, 'status', '') or '')
        if status in (WORKSPACE_STATUS_DONE, WORKSPACE_STATUS_TERMINATED):
            return f'it is {status} now'
        sessions = self._get_session_manager()
        chat = sessions.get_record(task_id) if sessions is not None else None
        if not str(getattr(chat, 'agent_session_id', '') or '').strip():
            return 'it has no conversation to continue'
        return ''
