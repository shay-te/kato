from __future__ import annotations
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from kato.helpers.shell_status_utils import (
    clear_active_inline_status,
)
from kato.validation.base import ValidationBase
from kato.helpers.retry_utils import is_retryable_exception
from kato.helpers.text_utils import normalized_text
from kato.helpers.runtime_identity_utils import runtime_source_fingerprint

if TYPE_CHECKING:
    from kato.data_layers.service.implementation_service import ImplementationService
    from kato.validation.repository_connections import (
        RepositoryConnectionsValidator,
    )
    from kato.data_layers.service.task_service import TaskService
    from kato.data_layers.service.testing_service import TestingService


@dataclass(frozen=True)
class DependencyValidationStep:
    service_name: str
    validate: Callable[[], None]
    max_retries: int


class StartupDependencyValidator(ValidationBase):
    def __init__(
        self,
        repository_connections_validator: RepositoryConnectionsValidator,
        task_service: TaskService,
        implementation_service: ImplementationService,
        testing_service: TestingService,
        skip_testing: bool,
    ) -> None:
        self._repository_connections_validator = repository_connections_validator
        self._task_service = task_service
        self._implementation_service = implementation_service
        self._testing_service = testing_service
        self._skip_testing = bool(skip_testing)

    def validate(self, logger) -> None:
        dependency_steps = self._dependency_steps()
        total_steps = len(dependency_steps) + 1
        self._validate_repositories(
            logger,
            current_step=1,
            total_steps=total_steps,
        )

        summaries: list[str] = []
        details: list[str] = []
        for current_step, step in enumerate(dependency_steps, start=2):
            self._collect_validation_result(
                logger,
                step,
                summaries,
                details,
                current_step=current_step,
                total_steps=total_steps,
            )

        if details:
            raise RuntimeError(
                'startup dependency validation failed:\n\n'
                + '\n'.join(f'- {summary}' for summary in summaries)
                + '\n\nDetails:\n\n'
                + '\n\n'.join(details)
            )

    @staticmethod
    def validate_startup_configuration(open_cfg) -> None:
        StartupDependencyValidator._validate_secret_project_path(open_cfg)
        StartupDependencyValidator._validate_runtime_source_fingerprint(open_cfg)

    def _validate_repositories(
        self,
        logger,
        *,
        current_step: int,
        total_steps: int,
    ) -> None:
        try:
            self._run_validation_step(
                self._repository_connections_validator.validate,
                status_text=(
                    f'Validating connection ({current_step}/{total_steps}): '
                    f'{self._repository_validation_label()}'
                ),
                logger=logger,
            )
        except Exception as exc:
            clear_active_inline_status()
            logger.error('failed to validate repositories connection: %s', exc)
            raise RuntimeError(str(exc)) from exc

    def _dependency_steps(self) -> list[DependencyValidationStep]:
        steps = [
            DependencyValidationStep(
                self._task_service.provider_name,
                self._task_service.validate_connection,
                self._task_service.max_retries,
            ),
            DependencyValidationStep(
                'openhands',
                self._implementation_service.validate_connection,
                self._implementation_service.max_retries,
            ),
        ]
        if not self._skip_testing:
            steps.append(
                DependencyValidationStep(
                    'openhands_testing',
                    self._testing_service.validate_connection,
                    self._testing_service.max_retries,
                )
            )
        return steps

    def _collect_validation_result(
        self,
        logger,
        step: DependencyValidationStep,
        summaries: list[str],
        details: list[str],
        *,
        current_step: int,
        total_steps: int,
    ) -> None:
        try:
            self._run_validation_step(
                step.validate,
                status_text=(
                    f'Validating connection ({current_step}/{total_steps}): '
                    f'{step.service_name}'
                ),
                logger=logger,
            )
        except Exception as exc:
            summaries.append(
                self._validation_failure_summary(step.service_name, exc, step.max_retries)
            )
            details.append(f'[{step.service_name}]\n{exc}')

    @staticmethod
    def _run_validation_step(validate: Callable[[], None], *, status_text: str, logger) -> None:
        logger.info('%s', status_text)
        validate()

    def _repository_validation_label(self) -> str:
        repository_service = getattr(
            self._repository_connections_validator,
            '_repository_service',
            None,
        )
        repositories = getattr(repository_service, 'repositories', []) or []
        if not isinstance(repositories, (list, tuple)):
            return 'repositories'
        repository_ids = [
            str(getattr(repository, 'id', '') or '').strip()
            for repository in repositories
            if str(getattr(repository, 'id', '') or '').strip()
        ]
        if not repository_ids:
            return 'repositories'
        return f'repositories ({", ".join(repository_ids)})'

    @staticmethod
    def _validation_failure_summary(
        service_name: str,
        exc: Exception,
        max_retries: int,
    ) -> str:
        if is_retryable_exception(exc):
            return (
                f'unable to connect to {service_name} '
                f'(tried {max(1, max_retries)} times)'
            )
        return f'unable to validate {service_name}: {exc}'

    @staticmethod
    def _validate_secret_project_path(open_cfg) -> None:
        secret_project_path = normalized_text(
            getattr(getattr(open_cfg, 'workspace', None), 'secret_project_path', '')
        )
        if not secret_project_path:
            return
        if Path(secret_project_path).is_dir():
            return
        raise RuntimeError(
            f'missing required secret project path directory: {secret_project_path}'
        )

    @staticmethod
    def _validate_runtime_source_fingerprint(open_cfg) -> None:
        expected_source_fingerprint = normalized_text(
            getattr(open_cfg, 'source_fingerprint', '')
        )
        if not expected_source_fingerprint:
            return

        current_source_fingerprint = runtime_source_fingerprint()
        if current_source_fingerprint == expected_source_fingerprint:
            return

        raise RuntimeError(
            'startup dependency validation failed: '
            'Kato source fingerprint mismatch: '
            f'expected {expected_source_fingerprint}, '
            f'got {current_source_fingerprint}; '
            'rebuild the Kato image before running'
        )
