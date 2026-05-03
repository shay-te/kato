import unittest

from kato_core_lib.validation.base import ValidationBase
from kato_core_lib.validation.branch_publishability import (
    TaskBranchPublishabilityValidator,
)
from kato_core_lib.validation.branch_push import (
    TaskBranchPushValidator,
)
from kato_core_lib.validation.model_access import (
    TaskModelAccessValidator,
)
from kato_core_lib.validation.repository_connections import (
    RepositoryConnectionsValidator,
)
from kato_core_lib.validation.startup_dependency_validator import (
    StartupDependencyValidator,
)


class ValidationBaseTests(unittest.TestCase):
    def test_all_validators_inherit_from_validation_base(self) -> None:
        self.assertTrue(issubclass(TaskBranchPublishabilityValidator, ValidationBase))
        self.assertTrue(issubclass(TaskBranchPushValidator, ValidationBase))
        self.assertTrue(issubclass(TaskModelAccessValidator, ValidationBase))
        self.assertTrue(issubclass(RepositoryConnectionsValidator, ValidationBase))
        self.assertTrue(issubclass(StartupDependencyValidator, ValidationBase))
