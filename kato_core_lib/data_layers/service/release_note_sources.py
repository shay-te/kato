"""Gather what is known about a finished task, for its release note.

One job: read the facts, from the workspace kato kept for the task (title,
description, repositories, when it started, and the agent's own
``pr_description.md``) — and, when there is no workspace on this machine (a
plan step counted as done on another kato's task), from the tracker.

Read at the moment the task is done: the forget flow deletes the workspace
right after, so nothing here may be deferred.
"""

from __future__ import annotations

from pathlib import Path

from agent_core_lib.agent_core_lib.helpers.agent_prompt_utils import PR_DESCRIPTION_FILENAME

from kato_core_lib.data_layers.data.release_note_source import ReleaseNoteSource
from kato_core_lib.helpers.late_binding import provider_for
from kato_core_lib.helpers.logging_utils import configure_logger
from kato_core_lib.helpers.workspace_repo_utils import task_workspace_root

# Enough of the agent's PR description to know what changed.
_MAX_PR_DESCRIPTION_BYTES = 64 * 1024


class ReleaseNoteSources(object):
    def __init__(self, *, workspace_manager, task_service, logger=None) -> None:
        self._get_workspace_manager = provider_for(workspace_manager)
        self._get_task_service = provider_for(task_service)
        self.logger = logger or configure_logger('ReleaseNoteSources')

    def read(self, task_id: str) -> ReleaseNoteSource:
        """Everything known about ``task_id`` right now; at least its id."""
        record = self._workspace_record(task_id)
        if record is not None:
            return ReleaseNoteSource(
                task_id=task_id,
                title=str(getattr(record, 'task_summary', '') or ''),
                description=str(getattr(record, 'task_description', '') or ''),
                pr_description=self._pr_description(task_id),
                repositories=tuple(str(r) for r in getattr(record, 'repository_ids', None) or ()),
                started_at=float(getattr(record, 'created_at_epoch', 0.0) or 0.0),
            )
        task = self._tracker_task(task_id)
        return ReleaseNoteSource(
            task_id=task_id,
            title=str(getattr(task, 'summary', '') or ''),
            description=str(getattr(task, 'description', '') or ''),
        )

    def _workspace_record(self, task_id: str):
        manager = self._get_workspace_manager()
        if manager is None:
            return None
        try:
            return manager.get(task_id)
        except Exception:
            self.logger.exception('could not read the workspace of task %s', task_id)
            return None

    def _pr_description(self, task_id: str) -> str:
        root = task_workspace_root(self._get_workspace_manager(), task_id)
        if not root:
            return ''
        try:
            with open(Path(root) / PR_DESCRIPTION_FILENAME, 'rb') as handle:
                return handle.read(_MAX_PR_DESCRIPTION_BYTES).decode('utf-8', errors='replace')
        except OSError:
            return ''

    def _tracker_task(self, task_id: str):
        service = self._get_task_service()
        if service is None:
            return None
        try:
            return service.get_task(task_id)
        except Exception:
            self.logger.exception('could not read task %s from the tracker', task_id)
            return None
