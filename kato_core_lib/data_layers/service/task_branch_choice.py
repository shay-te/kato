"""Which branch a task may work on, however the name was given.

"New task", a ticket's ``kato:branch:<name>`` tag and a rename all pass through
``unusable_branch_reason``.

A ticket is held to more than a person at a keyboard, because anyone who can
edit a ticket can add a tag, and the branch is where kato pushes. So a ticket
only names a NEW branch, only before the task starts, and never a plan step's:
a plan finds a step's pull request by its ticket id, on every kato that follows
the plan. A tag that fails any of this is left out, and the task says why in its
preparation log. The task still runs, on its id, as it would with no tag.
"""

from __future__ import annotations

import logging

from git_core_lib.git_core_lib.helpers.branch_name_utils import branch_name_problem
from kato_core_lib.data_layers.service.plan_queue.step_footer import parse_footer
from kato_core_lib.helpers.kato_tag_utils import (
    branch_from_tag,
    build_branch_tag,
    task_branch,
)


_logger = logging.getLogger(__name__)


def unusable_branch_reason(
    branch: str,
    repositories: list,
    repository_service,
    workspace_service,
) -> str:
    """Why a task cannot work on ``branch``; '' when it can.

    The task's own branch cannot be the one it merges into: the agent's work
    would land on it with no pull request. Two tasks cannot share a branch: each
    has its own clone, and each would push over the other.
    """
    problem = branch_name_problem(branch)
    if problem:
        return f'{branch} cannot be a branch name: {problem}'
    wanted = branch.lower()
    for repository in repositories:
        try:
            destination = str(repository_service.destination_branch(repository))
        except ValueError as exc:
            return str(exc)
        if destination.lower() == wanted:
            return (
                f'{branch} is the branch {repository.id} merges into, so the work '
                f'would land on it with no pull request. Pick another name.'
            )
    for record in workspace_service.list_workspaces():
        if task_branch(record.task_id, record.tags).lower() == wanted:
            return f'task {record.task_id} already works on {branch}. Pick another name.'
    return ''


def record_branch_from_ticket(workspace_service, repository_service, task, repositories: list) -> None:
    """Take the ticket's ``kato:branch:`` tag into kato's own tags, if it may be used."""
    task_id = str(task.id)
    branch = _branch_on_ticket(task)
    if not branch:
        return
    recorded = task_branch(task_id, getattr(workspace_service.get(task_id), 'tags', None))
    if branch == recorded:
        return
    problem = _ticket_branch_problem(
        branch, recorded, task, repositories, repository_service, workspace_service,
    )
    if problem:
        message = f'⚠ the ticket names branch {branch}, not used: {problem} This task works on {recorded}.'
        _logger.warning('task %s: %s', task_id, message)
        workspace_service.append_preflight_log(task_id, message)
        return
    workspace_service.add_tag(task_id, build_branch_tag(branch))


def _branch_on_ticket(task) -> str:
    tags = getattr(task, 'tags', None)
    if not isinstance(tags, (list, tuple)):
        return ''
    return next((branch for branch in map(branch_from_tag, tags) if branch), '')


def _ticket_branch_problem(
    branch: str, recorded: str, task, repositories: list, repository_service, workspace_service,
) -> str:
    task_id = str(task.id)
    if recorded != task_id:
        return f'the task already works on {recorded}.'
    if parse_footer(getattr(task, 'description', '')) is not None:
        return "a plan's sub-task works on its ticket id, which is how a plan finds its pull request."
    if any(repository_service.has_local_branch(repository.local_path, task_id) for repository in repositories):
        return f'the task has already started on {task_id}.'
    reason = unusable_branch_reason(branch, repositories, repository_service, workspace_service)
    return reason or _branch_already_on_a_remote(branch, repositories, repository_service)


def _branch_already_on_a_remote(branch: str, repositories: list, repository_service) -> str:
    for repository in repositories:
        try:
            if repository_service.remote_has_branch(repository.local_path, branch, repository):
                return f'{branch} already exists on the remote of {repository.id}, and a ticket can only name a new branch.'
        except RuntimeError as exc:
            return f'could not ask the remote of {repository.id}: {exc}.'
    return ''
