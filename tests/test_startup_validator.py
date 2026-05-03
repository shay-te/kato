import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from kato_core_lib.validation.startup_dependency_validator import (
    StartupDependencyValidator,
)


class StartupDependencyValidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        # Label-formatting reads the *materialised* inventory at
        # ``_repositories`` to avoid forcing a lazy disk walk just to
        # enumerate names. Mirror that here.
        repository_service = SimpleNamespace(
            _repositories=[
                SimpleNamespace(id='client'),
                SimpleNamespace(id='backend'),
            ]
        )
        self.repository_connections_validator = Mock()
        self.repository_connections_validator._repository_service = repository_service
        self.task_service = SimpleNamespace(
            provider_name='youtrack',
            validate_connection=Mock(),
            max_retries=5,
        )
        self.implementation_service = SimpleNamespace(
            validate_connection=Mock(),
            max_retries=3,
        )
        self.testing_service = SimpleNamespace(
            validate_connection=Mock(),
            max_retries=4,
        )
        self.validator = StartupDependencyValidator(
            self.repository_connections_validator,
            self.task_service,
            self.implementation_service,
            self.testing_service,
            skip_testing=False,
        )
        self.logger = Mock()

    def test_validate_checks_repository_and_all_dependencies(self) -> None:
        self.validator.validate(self.logger)

        self.repository_connections_validator.validate.assert_called_once_with()
        self.task_service.validate_connection.assert_called_once_with()
        self.implementation_service.validate_connection.assert_called_once_with()
        self.testing_service.validate_connection.assert_called_once_with()
        self.logger.info.assert_any_call(
            '%s',
            'Validating connection (1/4): repositories (client, backend)',
        )
        self.logger.info.assert_any_call('%s', 'Validating connection (2/4): youtrack')
        self.logger.info.assert_any_call('%s', 'Validating connection (3/4): openhands')
        self.logger.info.assert_any_call('%s', 'Validating connection (4/4): openhands_testing')
        self.assertEqual(
            self.logger.info.call_args_list,
            [
                unittest.mock.call(
                    '%s',
                    'Validating connection (1/4): repositories (client, backend)',
                ),
                unittest.mock.call('%s', 'Validating connection (2/4): youtrack'),
                unittest.mock.call('%s', 'Validating connection (3/4): openhands'),
                unittest.mock.call('%s', 'Validating connection (4/4): openhands_testing'),
            ],
        )

    def test_validate_skips_testing_dependency_when_configured(self) -> None:
        validator = StartupDependencyValidator(
            self.repository_connections_validator,
            self.task_service,
            self.implementation_service,
            self.testing_service,
            skip_testing=True,
        )

        validator.validate(self.logger)

        self.repository_connections_validator.validate.assert_called_once_with()
        self.task_service.validate_connection.assert_called_once_with()
        self.implementation_service.validate_connection.assert_called_once_with()
        self.testing_service.validate_connection.assert_not_called()
        self.assertEqual(
            self.logger.info.call_args_list,
            [
                unittest.mock.call(
                    '%s',
                    'Validating connection (1/3): repositories (client, backend)',
                ),
                unittest.mock.call('%s', 'Validating connection (2/3): youtrack'),
                unittest.mock.call('%s', 'Validating connection (3/3): openhands'),
            ],
        )

    def test_validate_aggregates_dependency_failures(self) -> None:
        self.task_service.validate_connection.side_effect = ConnectionError('connection refused')
        self.testing_service.validate_connection.side_effect = RuntimeError('testing down')
        self.logger = Mock()

        with self.assertRaisesRegex(RuntimeError, 'startup dependency validation failed') as exc_context:
            self.validator.validate(self.logger)

        message = str(exc_context.exception)
        self.assertIn('- unable to connect to youtrack (tried 5 times)', message)
        self.assertIn('- unable to validate openhands_testing: testing down', message)
        self.assertIn('Details:', message)
        self.assertIn('[youtrack]', message)
        self.assertIn('connection refused', message)
        self.assertIn('[openhands_testing]', message)
        self.assertIn('testing down', message)
        self.logger.exception.assert_not_called()

    def test_validate_raises_when_repository_validation_fails(self) -> None:
        self.repository_connections_validator.validate.side_effect = RuntimeError('repo down')

        with self.assertRaisesRegex(RuntimeError, 'repo down') as exc_context:
            self.validator.validate(self.logger)

        self.repository_connections_validator.validate.assert_called_once_with()
        self.task_service.validate_connection.assert_not_called()
        self.implementation_service.validate_connection.assert_not_called()
        self.testing_service.validate_connection.assert_not_called()
        self.logger.error.assert_called_once()
        self.assertEqual(self.logger.error.call_args.args[0], 'failed to validate repositories connection: %s')
        self.assertIsInstance(self.logger.error.call_args.args[1], RuntimeError)
        self.assertEqual(str(self.logger.error.call_args.args[1]), 'repo down')
        self.assertIsInstance(exc_context.exception.__cause__, RuntimeError)
        self.assertEqual(str(exc_context.exception.__cause__), 'repo down')

    def test_validate_logs_progress_without_inline_spinner(self) -> None:
        self.validator.validate(self.logger)

        self.assertEqual(
            self.logger.info.call_args_list,
            [
                unittest.mock.call(
                    '%s',
                    'Validating connection (1/4): repositories (client, backend)',
                ),
                unittest.mock.call('%s', 'Validating connection (2/4): youtrack'),
                unittest.mock.call('%s', 'Validating connection (3/4): openhands'),
                unittest.mock.call('%s', 'Validating connection (4/4): openhands_testing'),
            ],
        )

    def test_validate_uses_agent_backend_label_for_implementation_steps(self) -> None:
        validator = StartupDependencyValidator(
            self.repository_connections_validator,
            self.task_service,
            self.implementation_service,
            self.testing_service,
            skip_testing=False,
            agent_backend='claude',
        )

        validator.validate(self.logger)

        self.assertEqual(
            self.logger.info.call_args_list,
            [
                unittest.mock.call(
                    '%s',
                    'Validating connection (1/4): repositories (client, backend)',
                ),
                unittest.mock.call('%s', 'Validating connection (2/4): youtrack'),
                unittest.mock.call('%s', 'Validating connection (3/4): claude'),
                unittest.mock.call('%s', 'Validating connection (4/4): claude_testing'),
            ],
        )

    def test_validate_surfaces_claude_binary_missing_with_backend_label(self) -> None:
        validator = StartupDependencyValidator(
            self.repository_connections_validator,
            self.task_service,
            self.implementation_service,
            self.testing_service,
            skip_testing=True,
            agent_backend='claude',
        )
        self.implementation_service.validate_connection = Mock(
            side_effect=RuntimeError(
                'Claude CLI binary "claude" was not found on PATH. '
                'Install Claude Code from https://docs.claude.com/en/docs/claude-code/setup'
            )
        )

        with self.assertRaisesRegex(RuntimeError, 'startup dependency validation failed') as exc_context:
            validator.validate(self.logger)

        message = str(exc_context.exception)
        self.assertIn('- unable to validate claude:', message)
        self.assertIn('Claude CLI binary "claude" was not found on PATH', message)
        self.assertIn('[claude]', message)
