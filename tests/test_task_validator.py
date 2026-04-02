import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from openhands_agent.data_layers.service.validation.branch_publishability import (
    TaskBranchPublishabilityValidator,
)
from openhands_agent.data_layers.service.validation.branch_push import (
    TaskBranchPushValidator,
)
from openhands_agent.data_layers.service.validation.model_access import (
    TaskModelAccessValidator,
)
from utils import build_task


class TaskValidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.implementation_service = Mock()
        self.testing_service = Mock()
        self.repository_service = Mock()
        self.model_validator = TaskModelAccessValidator(
            self.implementation_service,
            self.testing_service,
            skip_testing=False,
        )
        self.push_validator = TaskBranchPushValidator(self.repository_service)
        self.publishability_validator = TaskBranchPublishabilityValidator(self.repository_service)
        self.task = build_task()
        self.repository = SimpleNamespace(id='client', local_path='/workspace/project/client')
        self.repositories = [self.repository]
        self.repository_branches = {'client': 'feature/proj-1/client'}

    def test_validate_model_access_checks_implementation_and_testing_services(self) -> None:
        self.model_validator.validate(self.task)

        self.implementation_service.validate_model_access.assert_called_once_with()
        self.testing_service.validate_model_access.assert_called_once_with()

    def test_validate_model_access_skips_testing_when_configured(self) -> None:
        validator = TaskModelAccessValidator(
            self.implementation_service,
            self.testing_service,
            skip_testing=True,
        )

        validator.validate(self.task)

        self.implementation_service.validate_model_access.assert_called_once_with()
        self.testing_service.validate_model_access.assert_not_called()

    def test_validate_branch_push_access_checks_each_repository(self) -> None:
        self.push_validator.validate(self.repositories, self.repository_branches)

        self.repository_service._ensure_branch_is_pushable.assert_called_once_with(
            '/workspace/project/client',
            'feature/proj-1/client',
            self.repository,
        )

    def test_validate_branch_push_access_rejects_missing_branch_name(self) -> None:
        with self.assertRaisesRegex(ValueError, 'missing task branch name for repository client'):
            self.push_validator.validate(self.repositories, {})

    def test_validate_branch_publishability_checks_each_repository(self) -> None:
        self.repository_service.destination_branch.return_value = 'master'

        self.publishability_validator.validate(self.repositories, self.repository_branches)

        self.repository_service.destination_branch.assert_called_once_with(self.repository)
        self.repository_service._ensure_branch_has_task_changes.assert_called_once_with(
            '/workspace/project/client',
            'feature/proj-1/client',
            'master',
        )

    def test_validate_branch_publishability_rejects_missing_branch_name(self) -> None:
        self.repository_service.destination_branch.return_value = 'master'

        with self.assertRaisesRegex(ValueError, 'missing task branch name for repository client'):
            self.publishability_validator.validate(self.repositories, {})
