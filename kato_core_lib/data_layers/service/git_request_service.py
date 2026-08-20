"""Perform the git operations the agent asked kato to do.

The counterpart to :mod:`kato_core_lib.helpers.git_request`: that module
decides whether a request is well-formed and whether the operator has to
see it; this one runs the approved ones through kato's own hardened git
client, so the operation gets the hook-disabling, remote-pinning and argv
validation the agent's shell would not.

Nothing here publishes. ``push`` and ``open_pull_request`` are refused at
parse time and never reach an executor — kato publishes on an operator
action and on nothing else, and a request file is not a second door into
that decision.
"""

from __future__ import annotations

import os

from kato_core_lib.helpers.git_request import (
    GitRequest,
    GitRequestError,
)
from kato_core_lib.helpers.logging_utils import configure_logger


class GitRequestService(object):
    """Execute a validated :class:`GitRequest` against one task clone."""

    def __init__(self, repository_service, workspace_manager, logger=None) -> None:
        if repository_service is None:
            raise ValueError('repository_service is required')
        if workspace_manager is None:
            raise ValueError('workspace_manager is required')
        self._repository_service = repository_service
        self._workspace_manager = workspace_manager
        self.logger = logger or configure_logger(self.__class__.__name__)

    def execute(self, task_id: str, request: GitRequest) -> str:
        """Run ``request`` and return an operator/agent-readable summary.

        Raises :class:`GitRequestError` when the request cannot be carried
        out — the message goes back to the agent, so it says what to do
        instead rather than only what failed.
        """
        local_path = self._clone_path(task_id, request.repository_id)
        handler = getattr(self, f'_do_{request.operation}', None)
        if handler is None:   # pragma: no cover - parse_request gates this
            raise GitRequestError(f'unsupported operation {request.operation!r}')
        self.logger.info(
            'git request for task %s: %s on %s (%s)',
            task_id, request.operation, request.repository_id or '<repo>',
            request.reason,
        )
        return handler(local_path, request)

    def _clone_path(self, task_id: str, repository_id: str) -> str:
        task = str(task_id or '').strip()
        repository = str(repository_id or '').strip()
        if not task:
            raise GitRequestError('the request has no task')
        if not repository:
            raise GitRequestError(
                'the request needs a "repository_id" naming which repository '
                'clone to act on.',
            )
        path = self._workspace_manager.repository_path(task, repository)
        if not path or not os.path.isdir(path):
            raise GitRequestError(
                f'no clone for repository {repository!r} in this task — check '
                'the repository id against the workspace folders you were '
                'given.',
            )
        return path

    # --- operations -------------------------------------------------------
    #
    # Each returns the sentence the operator and the agent both read, so a
    # request that ran but changed nothing says so rather than reporting a
    # success that did nothing.

    def _do_stage(self, local_path: str, request: GitRequest) -> str:
        paths = self._safe_paths(request)
        self._repository_service._run_git(
            local_path, ['add', '--', *paths], 'failed to stage files',
        )
        return f'staged {len(paths)} path(s): {", ".join(paths)}'

    def _do_unstage(self, local_path: str, request: GitRequest) -> str:
        paths = self._safe_paths(request)
        self._repository_service._run_git(
            local_path, ['restore', '--staged', '--', *paths],
            'failed to unstage files',
        )
        return f'unstaged {len(paths)} path(s): {", ".join(paths)}'

    def _do_commit(self, local_path: str, request: GitRequest) -> str:
        message = request.message or request.reason
        if not self._repository_service._working_tree_status(local_path):
            return 'nothing to commit — the working tree is clean'
        self._repository_service._run_git(
            local_path, ['add', '-A'], 'failed to stage changes',
        )
        self._repository_service._run_git(
            local_path, ['commit', '-m', message], 'failed to commit',
        )
        return f'committed: {message}'

    def _do_create_branch(self, local_path: str, request: GitRequest) -> str:
        branch = self._branch(request)
        self._repository_service._run_git(
            local_path, ['checkout', '-b', branch], 'failed to create branch',
        )
        return f'created and switched to branch {branch}'

    def _do_switch_branch(self, local_path: str, request: GitRequest) -> str:
        branch = self._branch(request)
        self._repository_service._run_git(
            local_path, ['checkout', branch], 'failed to switch branch',
        )
        return f'switched to branch {branch}'

    def _do_clean(self, local_path: str, request: GitRequest) -> str:
        # Path-scoped only. ``git clean -fd`` with no pathspec deletes every
        # untracked file in the clone — including anything the agent has
        # written but not yet had committed, which is unrecoverable.
        paths = self._safe_paths(request)
        self._repository_service._run_git(
            local_path, ['clean', '-fd', '--', *paths],
            'failed to clean untracked files',
        )
        return f'removed untracked files under: {", ".join(paths)}'

    # --- shared validation ------------------------------------------------

    @staticmethod
    def _branch(request: GitRequest) -> str:
        if not request.branch:
            raise GitRequestError(
                f'the {request.operation!r} request needs a "branch" field.',
            )
        return request.branch

    @staticmethod
    def _safe_paths(request: GitRequest) -> list[str]:
        """Reuse the git client's pathspec guard — same rule, one place.

        Without it, ``paths: ["."]`` on a clean or stage request would widen
        the operation to the whole clone, which is exactly what the guard on
        the discard path exists to prevent.
        """
        from git_core_lib.git_core_lib.client.git_client import GitClientMixin
        paths = GitClientMixin._safe_restore_pathspecs(request.paths)
        if not paths:
            raise GitRequestError(
                f'the {request.operation!r} request needs a "paths" list of '
                'plain repo-relative file paths (no ".", "..", globs, or '
                'absolute paths).',
            )
        return paths
