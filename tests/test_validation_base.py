import unittest

from openhands_agent.validation.base import ValidationBase
from openhands_agent.validation.branch_publishability import (
    TaskBranchPublishabilityValidator,
)
from openhands_agent.validation.branch_push import (
    TaskBranchPushValidator,
)
from openhands_agent.validation.model_access import (
    TaskModelAccessValidator,
)
from openhands_agent.validation.repository_connections import (
    RepositoryConnectionsValidator,
)
from openhands_agent.validation.startup_dependency_validator import (
    StartupDependencyValidator,
)


class ValidationBaseTests(unittest.TestCase):
    def test_all_validators_inherit_from_validation_base(self) -> None:
        self.assertTrue(issubclass(TaskBranchPublishabilityValidator, ValidationBase))
        self.assertTrue(issubclass(TaskBranchPushValidator, ValidationBase))
        self.assertTrue(issubclass(TaskModelAccessValidator, ValidationBase))
        self.assertTrue(issubclass(RepositoryConnectionsValidator, ValidationBase))
        self.assertTrue(issubclass(StartupDependencyValidator, ValidationBase))
