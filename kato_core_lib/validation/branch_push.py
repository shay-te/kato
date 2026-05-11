from __future__ import annotations

from typing import TYPE_CHECKING

from kato_core_lib.validation.base import ValidationBase

if TYPE_CHECKING:
    from kato_core_lib.data_layers.service.repository_service import RepositoryService


class TaskBranchPushValidator(ValidationBase):
    def __init__(self, repository_service: RepositoryService) -> None:
        self._repository_service = repository_service

    def validate(
        self,
        repositories: list[object],
        repository_branches: dict[str, str],
    ) -> None:
        self._validate_repositories(repositories, repository_branches)

    def _validate_repository(self, repository: object, branch_name: str) -> None:
        self._repository_service._ensure_branch_is_pushable(
            repository.local_path,
            branch_name,
            repository,
        )
