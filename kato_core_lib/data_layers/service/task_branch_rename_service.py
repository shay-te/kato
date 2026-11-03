"""Rename a task's branch, in every clone and on the remote where it was pushed.

Locally it is one ``git branch -m`` per clone and one tag. On a remote, git has
no rename: the commit the old branch points at is pushed under the new name,
then the old branch is deleted. Only what the remote already holds moves, so a
rename never publishes a commit nobody pushed.

Refused where a rename would lose or break something: the remote holds commits
this clone does not, or a pull request is open from the branch (deleting its
branch closes it on GitHub and strands it elsewhere). A remote or a pull request
API that cannot be asked is a no.

All or nothing until the tag is saved: a repository left on the old name would
have its work pushed to a branch the task no longer knows. Deleting the old
remote branches comes last; one that fails leaves the work safe on the new name
and is reported, never hidden.
"""

from __future__ import annotations

import copy
import logging
import re

from kato_core_lib.data_layers.service.plan_queue.step_footer import parse_footer
from kato_core_lib.data_layers.service.task_branch_choice import unusable_branch_reason
from kato_core_lib.helpers.kato_tag_utils import build_branch_tag, task_branch
from kato_core_lib.helpers.late_binding import provider_for


_logger = logging.getLogger(__name__)

# A commit id as ``ls-remote`` prints it (SHA-1 or SHA-256). It goes on a git
# command line, so nothing else may.
_COMMIT_ID = re.compile(r'[0-9a-f]{40}(?:[0-9a-f]{24})?')


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
        """Rename the task's branch everywhere it is, or change nothing at all."""
        task_id = str(task_id or '').strip()
        new_name = str(new_name or '').strip()
        record = self._workspace_manager.get(task_id) if self._workspace_manager else None
        if record is None:
            raise BranchRenameRefused(f'task {task_id} has no workspace.')
        old_name = task_branch(task_id, record.tags)
        if new_name == old_name:
            return _result(old_name, [], [], [])
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
        plan = self._plan(repositories, old_name, new_name)
        pushed = self._rename_everywhere(task_id, plan, old_name, new_name)
        problems = self._finish_on_remotes(pushed, old_name, new_name)
        return _result(
            new_name,
            [str(repository.id) for repository, _commit in plan],
            [str(repository.id) for repository, _commit in pushed],
            problems,
        )

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

    def _plan(self, repositories: list, old_name: str, new_name: str) -> list[tuple]:
        """``(repository, remote commit or '')`` for each clone holding the branch.

        Every check runs before anything moves, in every repository.
        """
        plan = []
        for repository in repositories:
            path = repository.local_path
            service = self._repository_service
            if self._remote_commit(repository, new_name) or service.has_local_branch(path, new_name):
                raise BranchRenameRefused(f'{repository.id} already has a branch {new_name}.')
            commit = self._remote_commit(repository, old_name)
            here = service.has_local_branch(path, old_name)
            if commit:
                self._refuse_a_remote_rename_that_loses_something(repository, old_name, commit, here)
            if here:
                plan.append((repository, commit))
        return plan

    def _remote_commit(self, repository, branch: str) -> str:
        try:
            commit = self._repository_service.remote_branch_commit(
                repository.local_path, branch, repository,
            )
        except RuntimeError as exc:
            raise BranchRenameRefused(
                f'could not ask the remote of {repository.id}, so kato cannot tell '
                f'whether {branch} is on it: {exc}',
            ) from exc
        if commit and not _COMMIT_ID.fullmatch(commit):
            raise BranchRenameRefused(
                f'the remote of {repository.id} named an unexpected commit for {branch}: {commit!r}.',
            )
        return commit

    def _refuse_a_remote_rename_that_loses_something(
        self, repository, old_name: str, commit: str, here: bool,
    ) -> None:
        if not here:
            raise BranchRenameRefused(
                f'{old_name} is on the remote of {repository.id} but not in this clone. '
                'Pull first, so nothing on the remote is lost.',
            )
        if not self._repository_service.branch_contains(repository.local_path, old_name, commit):
            raise BranchRenameRefused(
                f'{old_name} on the remote of {repository.id} has commits this clone does not '
                'have. Pull first, so nothing on the remote is lost.',
            )
        try:
            pull_requests = self._repository_service.pull_request_states(repository, source_branch=old_name)
        except Exception as exc:
            raise BranchRenameRefused(
                f'kato could not check {repository.id} for a pull request from {old_name}, and '
                f'renaming the branch would close or break an open one: {exc}',
            ) from exc
        open_ones = [pr for pr in pull_requests if str(pr.get('state', '')).lower() == 'open']
        if open_ones:
            where = str(open_ones[0].get('url') or open_ones[0].get('id') or '')
            raise BranchRenameRefused(
                f'a pull request is open from {old_name} on {repository.id}'
                f'{f" ({where})" if where else ""}. Renaming the branch would close or break it. '
                'Merge or close it first.',
            )

    def _rename_everywhere(self, task_id: str, plan: list, old_name: str, new_name: str) -> list:
        """Rename locally, push the new names, save the tag. Any failure puts
        every step back, and the remotes are left as they were."""
        renamed, pushed = [], []
        try:
            for repository, commit in plan:
                self._repository_service.rename_local_branch(repository.local_path, old_name, new_name)
                renamed.append(repository)
            for repository, commit in plan:
                if commit:
                    self._repository_service.push_commit_as_new_branch(
                        repository.local_path, commit, new_name, repository,
                    )
                    pushed.append((repository, commit))
            self._workspace_manager.replace_tag(
                task_id, build_branch_tag(old_name), build_branch_tag(new_name),
            )
        except Exception as exc:
            problems = self._put_back(renamed, pushed, old_name, new_name)
            raise BranchRenameRefused(
                f'the rename failed, and every repository was put back: {exc}' if not problems
                else f'the rename failed: {exc}. Not put back: {"; ".join(problems)}',
            ) from exc
        return pushed

    def _put_back(self, renamed: list, pushed: list, old_name: str, new_name: str) -> list[str]:
        problems = []
        for repository, commit in pushed:
            try:
                self._repository_service.delete_remote_branch(
                    repository.local_path, new_name, commit, repository,
                )
            except Exception:
                _logger.exception('could not delete %s from the remote of %s', new_name, repository.id)
                problems.append(f'{new_name} is still on the remote of {repository.id}')
        for repository in renamed:
            try:
                self._repository_service.rename_local_branch(repository.local_path, new_name, old_name)
            except Exception:
                _logger.exception('could not put %s back on %s', repository.id, old_name)
                problems.append(f'{repository.id} is still on {new_name}')
        return problems

    def _finish_on_remotes(self, pushed: list, old_name: str, new_name: str) -> list[str]:
        """Track the new branch, delete the old one. The work is already safe
        on the new name, so a failure here is reported, not undone."""
        problems = []
        for repository, commit in pushed:
            try:
                self._repository_service.set_upstream_branch(repository.local_path, new_name)
            except Exception as exc:
                _logger.exception('could not track origin/%s in %s', new_name, repository.id)
                problems.append(f'{repository.id} does not track origin/{new_name} yet: {exc}')
            try:
                self._repository_service.delete_remote_branch(
                    repository.local_path, old_name, commit, repository,
                )
            except Exception as exc:
                _logger.exception('could not delete %s from the remote of %s', old_name, repository.id)
                problems.append(
                    f'{old_name} is still on the remote of {repository.id}: {exc}. '
                    f'The work is on {new_name}; delete {old_name} there by hand.',
                )
        return problems


def _result(branch: str, renamed: list, on_remote: list, problems: list) -> dict[str, object]:
    return {'branch': branch, 'renamed': renamed, 'renamed_on_remote': on_remote, 'problems': problems}


def _clone_of(repository, path) -> object:
    clone = copy.copy(repository)
    clone.local_path = str(path)
    return clone
