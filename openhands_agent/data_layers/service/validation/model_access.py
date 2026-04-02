from __future__ import annotations

from typing import TYPE_CHECKING

from openhands_agent.data_layers.service.validation.base import ValidationBase

if TYPE_CHECKING:
    from openhands_agent.data_layers.service.implementation_service import ImplementationService
    from openhands_agent.data_layers.service.testing_service import TestingService


class TaskModelAccessValidator(ValidationBase):
    def __init__(
        self,
        implementation_service: ImplementationService,
        testing_service: TestingService,
        skip_testing: bool,
    ) -> None:
        self._implementation_service = implementation_service
        self._testing_service = testing_service
        self._skip_testing = bool(skip_testing)

    def validate(self, task) -> None:
        self._implementation_service.validate_model_access()
        if not self._skip_testing:
            self._testing_service.validate_model_access()
