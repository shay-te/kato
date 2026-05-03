from __future__ import annotations

from typing import TYPE_CHECKING

from kato_core_lib.validation.base import ValidationBase

if TYPE_CHECKING:
    from kato_core_lib.data_layers.service.repository_service import RepositoryService


class RepositoryConnectionsValidator(ValidationBase):
    def __init__(self, repository_service: RepositoryService) -> None:
        self._repository_service = repository_service

    def validate(self) -> None:
        # Order mirrors the pre-lazy validator so callers still see
        # inventory errors (duplicate id / alias) before git-executable
        # errors. Per-repo git-access checks only run when we already
        # have an inventory in hand — kato defers
        # ``repository_root_path`` auto-discovery to the first task,
        # so on a typical boot with only ``REPOSITORY_ROOT_PATH``
        # configured we skip the per-repo walk here and the per-task
        # preflight handles those checks via
        # ``RepositoryService._prepare_task_repository``.
        loaded = self._repository_service._repositories
        if loaded is not None:
            self._repository_service._ensure_repositories()
        if hasattr(self._repository_service, '_validate_git_executable'):
            self._repository_service._validate_git_executable()
        if loaded is None:
            return
        for repository in loaded:
            self._repository_service._prepare_repository_access(repository)
            if hasattr(self._repository_service, '_validate_repository_git_access'):
                self._repository_service._validate_repository_git_access(repository)
