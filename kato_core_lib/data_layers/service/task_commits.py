"""Where each of a task's repositories stands: the commit its clone is on.

The Slack post on Done keeps this reading, so a later Done can tell whether
anything was committed in the task since it was last announced (post it
again) or not ("already posted"). A commit is how a file change reaches the
team: Done commits the agent's edits before it pushes them.

``None`` means "cannot tell": no workspace, a repository with no clone on
disk, or git could not answer for one of them. A caller must never read that
as "changed" — it would post the same task twice.
"""

from __future__ import annotations

from pathlib import Path

from kato_core_lib.helpers.late_binding import provider_for
from kato_core_lib.helpers.logging_utils import configure_logger

#: ``{repository id: (commit, committed_at)}`` — see ``GitClientMixin.last_commit``.
TaskCommitReading = dict[str, tuple[str, int]]


class TaskCommits(object):
    def __init__(self, *, workspace_manager, git, logger=None) -> None:
        self._get_workspace_manager = provider_for(workspace_manager)
        self._get_git = provider_for(git)
        self.logger = logger or configure_logger('TaskCommits')

    def read(self, task_id: str) -> TaskCommitReading | None:
        """Each repository's last commit, or None when any cannot be read."""
        task = str(task_id or '').strip()
        manager = self._get_workspace_manager()
        git = self._get_git()
        if not task or manager is None or git is None:
            return None
        try:
            record = manager.get(task)
            repository_ids = [
                str(repository_id).strip()
                for repository_id in (getattr(record, 'repository_ids', None) or ())
                if str(repository_id or '').strip()
            ]
            if not repository_ids:
                return None
            reading: TaskCommitReading = {}
            for repository_id in repository_ids:
                path = Path(manager.repository_path(task, repository_id))
                if not (path / '.git').exists():
                    return None
                reading[repository_id] = git.last_commit(str(path))
            return reading
        except Exception as exc:
            self.logger.warning('could not read the commits of task %s: %s', task, exc)
            return None
