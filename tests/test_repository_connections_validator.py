import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from kato_core_lib.validation.repository_connections import (
    RepositoryConnectionsValidator,
)


class RepositoryConnectionsValidatorTests(unittest.TestCase):
    """Validator now skips per-repo work when the inventory hasn't been loaded.

    The lazy refactor means the validator only iterates explicitly-loaded
    repositories. Auto-discovery + per-repo git checks fire at first task
    pickup, not at boot. These tests lock that lazy contract.
    """

    def _build_service(self, *, repositories=None):
        return SimpleNamespace(
            _validate_inventory=Mock(),
            _validate_git_executable=Mock(),
            _ensure_repositories=Mock(),
            _prepare_repository_access=Mock(),
            _validate_repository_git_access=Mock(),
            _repositories=repositories,
            repositories=repositories or [],
        )

    def test_validate_skips_per_repo_work_when_inventory_not_yet_loaded(self) -> None:
        service = self._build_service(repositories=None)
        validator = RepositoryConnectionsValidator(service)

        validator.validate()

        service._validate_git_executable.assert_called_once_with()
        service._prepare_repository_access.assert_not_called()
        service._validate_repository_git_access.assert_not_called()

    def test_validate_iterates_explicitly_loaded_repositories(self) -> None:
        service = self._build_service(repositories=['repo-1', 'repo-2'])
        validator = RepositoryConnectionsValidator(service)

        validator.validate()

        service._ensure_repositories.assert_called_once_with()
        service._validate_git_executable.assert_called_once_with()
        service._prepare_repository_access.assert_has_calls(
            [unittest.mock.call('repo-1'), unittest.mock.call('repo-2')], any_order=True,
        )
        service._validate_repository_git_access.assert_has_calls(
            [unittest.mock.call('repo-1'), unittest.mock.call('repo-2')], any_order=True,
        )
