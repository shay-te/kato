"""Create a task in kato itself — no issue tracker — and get it to work.

The operator fills the "New task" form (title, description, repositories, how
to start). This service:

1. checks everything BEFORE writing anything — the repositories exist and REP
   has approved them (the same check "+ Add task" and the scan loop use) — so
   a refused task leaves no record, no folder and no tab behind;
2. records it in the local task store as IN PROGRESS (the operator started it,
   as moving a ticket to In Progress does) and creates its workspace record,
   which is what makes its tab appear at once, still "provisioning";
3. ``prepare`` then clones the repositories and checks out the task branch —
   through the exact steps a ``kato:wait-planning`` ticket takes, so a local
   task can never end up branching inside the operator's own checkouts.

What the chat is told first depends on how the operator chose to start it.
Its task definition (title + description) is prepended by the chat spawn
itself, so the opening message only says what to do with it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from kato_core_lib.data_layers.data.task import Task
from kato_core_lib.data_layers.service.local_task_routing import (
    LocalAwareTaskService,
    local_task_as_task,
)
from kato_core_lib.helpers.kato_tag_utils import build_repository_tag
from kato_core_lib.helpers.logging_utils import configure_logger
from local_task_core_lib.local_task_core_lib.data.local_task import LocalTaskState

START_PLAN = 'plan'
START_IMPLEMENT = 'implement'
START_CHAT = 'chat'
START_MODES = (START_PLAN, START_IMPLEMENT, START_CHAT)

# The agent mode each start runs in ('' = kato's configured default mode).
START_PERMISSION_MODE = {START_PLAN: 'plan', START_IMPLEMENT: '', START_CHAT: ''}

_OPENING_MESSAGE = {
    START_PLAN: (
        'Plan this task. Read the code it touches, then propose a step-by-step '
        'implementation plan. Do not change anything until I approve the plan.'
    ),
    START_IMPLEMENT: 'Implement this task.',
}


def opening_message(start_mode: str) -> str:
    """What the chat is told first; '' for "just open the chat"."""
    return _OPENING_MESSAGE.get(start_mode, '')


class LocalTaskRejected(ValueError):
    """The task was not created; ``str()`` says why, for the operator."""

    def __init__(self, message: str, *, unapproved: Iterable[str] = ()) -> None:
        super().__init__(message)
        self.unapproved = list(unapproved)


class LocalTaskService(object):

    def __init__(
        self,
        *,
        task_service: LocalAwareTaskService,
        repository_service,
        workspace_manager,
        prepare_workspace: Callable[[Task], object],
        approval_check: Callable[[list], list[str]] | None = None,
        logger=None,
    ) -> None:
        self._task_service = task_service
        self._repository_service = repository_service
        self._workspace_manager = workspace_manager
        self._prepare_workspace = prepare_workspace
        self._approval_check = approval_check or _unapproved_repository_ids
        self.logger = logger or configure_logger(self.__class__.__name__)

    def create(self, summary: str, description: str, repository_ids: Iterable[str]) -> Task:
        summary = str(summary or '').strip()
        if not summary:
            raise LocalTaskRejected('give the task a title')
        repository_ids = _unique_ids(repository_ids)
        if not repository_ids:
            raise LocalTaskRejected('pick at least one repository')
        tags = [build_repository_tag(repository_id) for repository_id in repository_ids]
        repositories = self._resolve(Task(summary=summary, description=description, tags=tags))
        unapproved = self._approval_check(repositories)
        if unapproved:
            raise LocalTaskRejected(
                'these repositories are not approved for agent work yet: '
                f'{", ".join(unapproved)}. Approve them in Settings → Repositories.',
                unapproved=unapproved,
            )
        local = self._task_service.local_tasks.create(
            summary, description, tags=tags, state=LocalTaskState.IN_PROGRESS,
        )
        self._workspace_manager.create(
            task_id=local.id,
            task_summary=local.summary,
            task_description=local.description,
            repository_ids=[str(repository.id) for repository in repositories],
        )
        self.logger.info('local task %s created: %s', local.id, local.summary)
        return local_task_as_task(local)

    def prepare(self, task_id: str) -> bool:
        """Clone the task's repositories and check out its branch.

        True when the branch is ready. A failure is already on the task's
        preparation log; the caller then does not start the agent on it.
        """
        task = self._task_service.get_task(task_id)
        if task is None:
            self.logger.warning('local task %s is gone; nothing to prepare', task_id)
            return False
        context = self._prepare_workspace(task)
        return bool(getattr(context, 'expected_branch', ''))

    def _resolve(self, task: Task) -> list:
        try:
            return list(self._repository_service.resolve_task_repositories(task) or [])
        except Exception as exc:
            raise LocalTaskRejected(f'unknown repository: {exc}') from exc


def _unique_ids(repository_ids: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    kept: list[str] = []
    for repository_id in repository_ids or ():
        text = str(repository_id or '').strip()
        if text and text.lower() not in seen:
            seen.add(text.lower())
            kept.append(text)
    return kept


def _unapproved_repository_ids(repositories: list) -> list[str]:
    # The same check "+ Add task" and the autonomous preflight run, so the
    # three can never disagree about which repositories REP refuses.
    from kato_core_lib.data_layers.service.repository_approval_service import (
        RepositoryApprovalService,
    )
    return RepositoryApprovalService().unapproved_repository_ids(repositories)
