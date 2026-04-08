from __future__ import annotations

from abc import ABC, abstractmethod


class ValidationBase(ABC):
    @abstractmethod
    def validate(self, *args, **kwargs) -> None:
        raise NotImplementedError

    def _validate_repositories(
        self,
        repositories: list[object],
        repository_branches: dict[str, str],
    ) -> None:
        for repository in repositories:
            branch_name = repository_branches.get(repository.id, '')
            if not branch_name:
                raise ValueError(
                    f'missing task branch name for repository {repository.id}'
                )
            self._validate_repository(repository, branch_name)

    def _validate_repository(self, repository: object, branch_name: str) -> None:
        raise NotImplementedError
