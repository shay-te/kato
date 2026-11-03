"""Rename a task's branch, while the name still belongs only to this machine.

Once a branch is on a remote, its name is in every clone that fetched it and in
any pull request opened from it, so a rename would strand all of those. Before
that, it is one ``git branch -m`` per clone and one tag.

All or nothing: a repository left on the old name would have its work pushed to
a branch the task no longer knows.
"""

from __future__ import annotations

import copy
import logging

from kato_core_lib.data_layers.service.plan_queue.step_footer import parse_footer
from kato_core_lib.data_layers.service.task_branch_choice import unusable_branch_reason
from kato_core_lib.helpers.kato_tag_utils import build_branch_tag, task_branch
from kato_core_lib.helpers.late_binding import provider_for


_logger = logging.getLogger(__name__)


class BranchRenameRefused(ValueError):
    """The rename would not be safe; the message says why, for the operator."""


class TaskBranchRenameService(object):
    def __init__(self, repository_service, workspace_manager, session_manager=None) -> None:
        self._get_repository_service = provider_for(repository_service)
        self._get_workspace_manager = provider_for(workspace_manager)
        self._get_session_manager = provider_for(session_manager)

    @property
    def _repository_service(self):
        return self._get_repository_service()

    @property
    def _workspace_manager(self):
        return self._get_workspace_manager()

    @property
    def _session_manager(self):
        return self._get_session_manager()

    def rename_task_branch(self, task_id: str, new_name: str) -> dict[str, object]:
        """Rename the task's branch in every clone, or change nothing at all."""
        task_id = str(task_id or '').strip()
        new_name = str(new_name or '').strip()
        record = self._workspace_manager.get(task_id) if self._workspace_manager else None
        if record is None:
            raise BranchRenameRefused(f'task {task_id} has no workspace.')
        old_name = task_branch(task_id, record.tags)
        if new_name == old_name:
            return {'branch': old_name, 'renamed': []}
        if self._agent_is_working(task_id):
            raise BranchRenameRefused('the agent is working on this task. Rename it once the turn ends.')
        if parse_footer(getattr(record, 'task_description', '')) is not None:
            raise BranchRenameRefused(
                "a plan's sub-task works on its ticket id, which is how the plan finds its pull request.",
            )
        repositories = self._task_clones(task_id, record)
        reason = unusable_branch_reason(
            new_name, repositories, self._repository_service, self._workspace_manager,
        )
        if reason:
            raise BranchRenameRefused(reason)
        to_rename = self._refuse_what_a_remote_knows(repositories, old_name, new_name)
        self._rename_everywhere(task_id, to_rename, old_name, new_name)
        return {'branch': new_name, 'renamed': [str(repository.id) for repository in to_rename]}

    def _agent_is_working(self, task_id: str) -> bool:
        session = self._session_manager.get_session(task_id) if self._session_manager else None
        return bool(getattr(session, 'is_working', False))

    def _task_clones(self, task_id: str, record) -> list:
        repositories = []
        for repository_id in record.repository_ids:
            repository = self._repository_service.get_repository(repository_id)
            path = self._workspace_manager.repository_path(task_id, repository_id)
            if not (path / '.git').is_dir():
                raise BranchRenameRefused(f'{repository_id} has no clone on this task yet.')
            repositories.append(_clone_of(repository, path))
        return repositories

    def _refuse_what_a_remote_knows(self, repositories: list, old_name: str, new_name: str) -> list:
        """The clones that hold the branch. A remote that cannot be asked is a no."""
        to_rename = []
        for repository in repositories:
            path = repository.local_path
            service = self._repository_service
            if service.has_remote_tracking_branch(path, old_name) or self._on_remote(repository, old_name):
                raise BranchRenameRefused(
                    f'{old_name} has been pushed to {repository.id}, so its name is no '
                    'longer only here. A pull request or another clone may depend on it.',
                )
            if self._on_remote(repository, new_name) or service.has_local_branch(path, new_name):
                raise BranchRenameRefused(f'{repository.id} already has a branch {new_name}.')
            if service.has_local_branch(path, old_name):
                to_rename.append(repository)
        return to_rename

    def _on_remote(self, repository, branch: str) -> bool:
        try:
            return self._repository_service.remote_has_branch(repository.local_path, branch, repository)
        except RuntimeError as exc:
            raise BranchRenameRefused(
                f'could not ask the remote of {repository.id}, so kato cannot tell '
                f'whether {branch} is on it: {exc}',
            ) from exc

    def _rename_everywhere(self, task_id: str, repositories: list, old_name: str, new_name: str) -> None:
        renamed = []
        try:
            for repository in repositories:
                self._repository_service.rename_local_branch(repository.local_path, old_name, new_name)
                renamed.append(repository)
            self._workspace_manager.replace_tag(
                task_id, build_branch_tag(old_name), build_branch_tag(new_name),
            )
        except Exception as exc:
            problems = self._put_back(renamed, old_name, new_name)
            raise BranchRenameRefused(
                f'the rename failed, and every repository was put back: {exc}' if not problems
                else f'the rename failed: {exc}. Still on {new_name}, not put back: {", ".join(problems)}',
            ) from exc

    def _put_back(self, repositories: list, old_name: str, new_name: str) -> list[str]:
        problems = []
        for repository in repositories:
            try:
                self._repository_service.rename_local_branch(repository.local_path, new_name, old_name)
            except Exception:
                _logger.exception('could not put %s back on %s', repository.id, old_name)
                problems.append(str(repository.id))
        return problems


def _clone_of(repository, path) -> object:
    clone = copy.copy(repository)
    clone.local_path = str(path)
    return clone
