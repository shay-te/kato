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

    def test_validate_no_inventory_and_no_git_check_is_noop(self) -> None:
        """Covers branch 34->37: when ``_repositories`` is None AND the
        service lacks ``_validate_git_executable``, validate() falls
        through to the early ``return`` without doing any per-repo work."""
        service = SimpleNamespace(
            _ensure_repositories=Mock(),
            _prepare_repository_access=Mock(),
            _repositories=None,
            repositories=[],
        )
        validator = RepositoryConnectionsValidator(service)

        validator.validate()

        service._ensure_repositories.assert_not_called()
        service._prepare_repository_access.assert_not_called()

    def test_validate_one_skips_git_access_when_service_lacks_attr(self) -> None:
        """Covers branch 55->exit: ``_validate_repository_git_access``
        is optional; when absent, ``_validate_one`` returns after the
        ``_prepare_repository_access`` call."""
        service = SimpleNamespace(
            _validate_git_executable=Mock(),
            _ensure_repositories=Mock(),
            _prepare_repository_access=Mock(),
            _repositories=['repo-1'],
            repositories=['repo-1'],
        )
        # No ``_validate_repository_git_access`` attribute on the service.
        validator = RepositoryConnectionsValidator(service)

        validator.validate()

        service._prepare_repository_access.assert_called_once_with('repo-1')
        self.assertFalse(hasattr(service, '_validate_repository_git_access'))


class RepositoryConnectionsValidatorConcurrencyTests(unittest.TestCase):
    """Boot validation is network-bound, so its worker cap is load-bearing.

    Each per-repo check is a provider round-trip. At a cap of 8 a 62-repo
    inventory took ~8 waves, measured as a 22-second gap between the
    webserver binding and the first reconciliation line — the window in
    which kato is serving but not yet scanning.
    """

    def _service(self, repositories):
        return SimpleNamespace(
            _validate_git_executable=Mock(),
            _ensure_repositories=Mock(),
            _prepare_repository_access=Mock(),
            _validate_repository_git_access=Mock(),
            _repositories=repositories,
            repositories=repositories,
        )

    def test_an_empty_inventory_validates_instead_of_crashing(self) -> None:
        # ``ThreadPoolExecutor(max_workers=0)`` is a ValueError, so an empty
        # explicitly-loaded inventory used to take down the whole boot
        # validation rather than pass trivially.
        validator = RepositoryConnectionsValidator(self._service([]))
        validator.validate()  # must not raise

    def test_every_repository_is_checked_above_the_old_cap(self) -> None:
        repositories = [f'repo-{n}' for n in range(40)]
        service = self._service(repositories)

        RepositoryConnectionsValidator(service).validate()

        self.assertEqual(service._prepare_repository_access.call_count, 40)
        self.assertEqual(service._validate_repository_git_access.call_count, 40)

    def test_the_worker_cap_is_bounded_and_above_eight(self) -> None:
        from kato_core_lib.validation import repository_connections as module
        # Bounded on purpose: the checks mostly hit ONE provider, and kato
        # already runs a 180s scan cadence to stay under its rate limits.
        self.assertGreater(module._MAX_VALIDATION_WORKERS, 8)
        self.assertLessEqual(module._MAX_VALIDATION_WORKERS, 32)
