from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from kato_core_lib.validation.base import ValidationBase

if TYPE_CHECKING:
    from kato_core_lib.data_layers.service.repository_service import RepositoryService


class RepositoryConnectionsValidator(ValidationBase):
    def __init__(self, repository_service: RepositoryService) -> None:
        self._repository_service = repository_service

    def validate(self) -> None:
        loaded = self._repository_service._repositories
        has_git_check = hasattr(self._repository_service, '_validate_git_executable')

        if loaded is not None and has_git_check:
            # _ensure_repositories (inventory) and _validate_git_executable
            # are independent — run them concurrently.
            with ThreadPoolExecutor(max_workers=2) as executor:
                inv_future = executor.submit(self._repository_service._ensure_repositories)
                git_future = executor.submit(self._repository_service._validate_git_executable)
            inv_exc = inv_future.exception()
            git_exc = git_future.exception()
            if inv_exc:
                raise inv_exc
            if git_exc:
                raise git_exc
        elif loaded is not None:
            self._repository_service._ensure_repositories()
        elif has_git_check:
            self._repository_service._validate_git_executable()

        if loaded is None:
            return
        errors = self._validate_repositories_parallel(loaded)
        if errors:
            raise RuntimeError('\n'.join(errors))

    def _validate_repositories_parallel(self, repositories: list) -> list[str]:
        with ThreadPoolExecutor(max_workers=min(len(repositories), 8)) as executor:
            futures = [executor.submit(self._validate_one, repo) for repo in repositories]
        errors: list[str] = []
        for future in futures:
            exc = future.exception()
            if exc is not None:
                errors.append(str(exc))
        return errors

    def _validate_one(self, repository) -> None:
        self._repository_service._prepare_repository_access(repository)
        if hasattr(self._repository_service, '_validate_repository_git_access'):
            self._repository_service._validate_repository_git_access(repository)
