import types
import unittest
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

from kato_core_lib.helpers.agent_comment_classification import PRE_START_BLOCKING_PREFIXES
from kato_core_lib.data_layers.data.fields import PullRequestFields, StatusFields
from kato_core_lib.data_layers.service.task_preflight_service import (
    TaskPreflightService,
)
from kato_core_lib.helpers.task_context_utils import PreparedTaskContext
from agent_core_lib.agent_core_lib.helpers.agent_prompt_utils import (
    task_attachments_directory,
)
from tests.utils import build_task


class TaskPreflightServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        import os
        _ro = patch.dict(os.environ, {
            'KATO_READ_ONLY_REPOS_PATH': str(Path(tempfile.mkdtemp()) / 'ro.json'),
        })
        _ro.start()
        self.addCleanup(_ro.stop)
        self.task = build_task(
            summary='Update the client and backend flow',
            description='Implement the client and backend change',
        )
        self.repository = types.SimpleNamespace(
            id='client',
            local_path='/workspace/client',
            destination_branch='main',
        )
        self.repositories = [self.repository]
        self.prepared_task = PreparedTaskContext(
            branch_name='feature/proj-1/client',
            repositories=self.repositories,
            repository_branches={'client': 'feature/proj-1/client'},
        )
        self.task_model_access_validator = Mock()
        self.task_service = Mock()
        self.repository_service = Mock()
        self.repository_service.resolve_task_repositories.return_value = self.repositories
        self.repository_service.prepare_task_repositories.side_effect = lambda repositories: repositories
        self.repository_service.prepare_task_branches.side_effect = (
            lambda repositories, repository_branches: repositories
        )
        self.repository_service.build_branch_name.return_value = 'feature/proj-1/client'
        # Push-access partition: default to "writable" so the happy path keeps
        # every repo. Per-test overrides flip individual repos to read-only.
        self.repository_service.is_branch_pushable.return_value = True
        self.task_branch_push_validator = Mock()
        self.task_branch_publishability_validator = Mock()
        self.task_branch_push_validator.validate.return_value = None
        self.task_branch_publishability_validator.validate.return_value = None
        self.service = TaskPreflightService(
            task_model_access_validator=self.task_model_access_validator,
            task_service=self.task_service,
            repository_service=self.repository_service,
            task_branch_push_validator=self.task_branch_push_validator,
            task_branch_publishability_validator=self.task_branch_publishability_validator,
        )

    def test_prepare_task_execution_context_returns_prepared_context_on_happy_path(self) -> None:
        result = self.service.prepare_task_execution_context(self.task)

        self.assertIsInstance(result, PreparedTaskContext)
        self.assertEqual(result.branch_name, 'feature/proj-1/client')
        self.assertEqual(result.repositories, self.repositories)
        self.assertEqual(result.repository_branches, {'client': 'feature/proj-1/client'})
        self.task_model_access_validator.validate.assert_called_once_with(self.task)
        self.repository_service.resolve_task_repositories.assert_called_once_with(self.task)
        self.repository_service.prepare_task_repositories.assert_called_once_with(self.repositories)
        self.repository_service.build_branch_name.assert_called_once_with(
            self.task,
            self.repository,
        )
        self.repository_service.prepare_task_branches.assert_called_once_with(
            self.repositories,
            {'client': 'feature/proj-1/client'},
        )
        # Push access is now partitioned per-repo (not an all-or-nothing
        # validate). Every repo is pushable here, so none are read-only.
        self.repository_service.is_branch_pushable.assert_called_with(
            self.repository, 'feature/proj-1/client',
        )
        self.assertEqual(result.read_only_repository_ids, set())

    def test_prepare_task_execution_context_attaches_repository_agents_instructions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'AGENTS.md').write_text('repo-specific rule\n', encoding='utf-8')
            self.repository.local_path = str(root)

            result = self.service.prepare_task_execution_context(self.task)

        self.assertIsInstance(result, PreparedTaskContext)
        self.assertIn('Repository AGENTS.md instructions:', result.agents_instructions)
        self.assertIn('AGENTS.md:\nrepo-specific rule', result.agents_instructions)

    def test_prepare_task_execution_context_reports_model_access_failure(self) -> None:
        self.task_model_access_validator.validate.side_effect = RuntimeError('model offline')
        failure_handler = Mock()

        result = self.service.prepare_task_execution_context(
            self.task,
            task_failure_handler=failure_handler,
        )

        self.assertIsNone(result)
        failure_handler.assert_called_once()
        self.assertIs(failure_handler.call_args.args[0], self.task)
        self.assertIsInstance(failure_handler.call_args.args[1], RuntimeError)
        self.assertIsNone(failure_handler.call_args.args[2])
        self.repository_service.resolve_task_repositories.assert_not_called()

    def test_prepare_task_execution_context_routes_repository_resolution_failure(self) -> None:
        self.repository_service.resolve_task_repositories.side_effect = ValueError(
            'no configured repository matched task PROJ-1'
        )
        failure_handler = Mock()

        result = self.service.prepare_task_execution_context(
            self.task,
            repository_resolution_failure_handler=failure_handler,
        )

        self.assertIsNone(result)
        failure_handler.assert_called_once()
        self.assertIs(failure_handler.call_args.args[0], self.task)
        self.assertIsInstance(failure_handler.call_args.args[1], ValueError)
        self.assertIsNone(failure_handler.call_args.args[2])
        self.repository_service.prepare_task_repositories.assert_not_called()

    def test_prepare_task_execution_context_skips_thin_task_definition_with_handler(self) -> None:
        thin_task = build_task(summary='tiny', description='No description provided.')
        failure_handler = Mock()

        result = self.service.prepare_task_execution_context(
            thin_task,
            task_definition_failure_handler=failure_handler,
        )

        self.assertIsNone(result)
        failure_handler.assert_called_once_with(thin_task)
        self.repository_service.resolve_task_repositories.assert_called_once_with(thin_task)
        self.repository_service.prepare_task_repositories.assert_called_once_with(self.repositories)
        self.repository_service.prepare_task_branches.assert_not_called()
        self.task_branch_push_validator.validate.assert_not_called()

    def test_prepare_task_execution_context_skips_when_completion_comment_is_active(self) -> None:
        with patch.object(
            self.service,
            '_active_execution_blocking_comment',
            return_value='Kato completed task PROJ-1.',
        ), patch.object(self.service, '_prepare_task_start') as mock_prepare_task_start:
            result = self.service.prepare_task_execution_context(self.task)

        self.assertEqual(result[StatusFields.STATUS], StatusFields.SKIPPED)
        self.assertEqual(result['id'], self.task.id)
        mock_prepare_task_start.assert_not_called()
        self.task_model_access_validator.validate.assert_not_called()
        self.repository_service.resolve_task_repositories.assert_not_called()

    def test_prepare_task_execution_context_logs_active_blocking_comment_only_once(self) -> None:
        self.service.logger = Mock()

        with patch.object(
            self.service,
            '_active_execution_blocking_comment',
            return_value='Kato agent stopped working on this task: sandbox failed',
        ):
            first_result = self.service.prepare_task_execution_context(self.task)
            second_result = self.service.prepare_task_execution_context(self.task)

        self.assertEqual(first_result[StatusFields.STATUS], StatusFields.SKIPPED)
        self.assertEqual(second_result[StatusFields.STATUS], StatusFields.SKIPPED)
        self.task_model_access_validator.validate.assert_not_called()
        self.service.logger.info.assert_called_once_with(
            'skipping task %s because a prior Kato %s comment is still active: %s',
            self.task.id,
            'unknown',
            'Kato agent stopped working on this task: sandbox failed',
        )

    def test_prepare_task_execution_context_retries_when_prior_blocking_comment_clears(self) -> None:
        with patch.object(
            self.service,
            '_active_execution_blocking_comment',
            return_value=PRE_START_BLOCKING_PREFIXES[0],
        ), patch.object(
            self.service,
            '_prepare_task_start',
            return_value=self.prepared_task,
        ) as mock_prepare_task_start:
            result = self.service.prepare_task_execution_context(self.task)

        self.assertIs(result, self.prepared_task)
        mock_prepare_task_start.assert_called_once_with(self.task)

    def test_prepare_task_execution_context_skips_when_prior_blocking_comment_persists(self) -> None:
        with patch.object(
            self.service,
            '_active_execution_blocking_comment',
            return_value=PRE_START_BLOCKING_PREFIXES[0],
        ), patch.object(
            self.service,
            '_prepare_task_start',
            return_value=None,
        ) as mock_prepare_task_start:
            result = self.service.prepare_task_execution_context(self.task)

        self.assertEqual(result[StatusFields.STATUS], StatusFields.SKIPPED)
        self.assertEqual(result['id'], self.task.id)
        mock_prepare_task_start.assert_called_once_with(self.task)
        self.task_model_access_validator.validate.assert_not_called()

    def test_validate_task_branch_push_access_returns_false_when_no_repo_writable(self) -> None:
        # Every repo is un-pushable → nothing kato can publish → fail (no
        # failure handler given, so it just returns False).
        self.repository_service.is_branch_pushable.return_value = False

        result = self.service.validate_task_branch_push_access(self.task, self.prepared_task)

        self.assertFalse(result)
        self.assertEqual(self.prepared_task.read_only_repository_ids, {'client'})

    def test_validate_task_branch_push_access_marks_unpushable_repo_read_only_and_continues(self) -> None:
        # One pushable + one un-pushable repo: kato does NOT reject the task —
        # it marks the un-pushable repo read-only, notifies, and continues.
        writable = types.SimpleNamespace(id='client', local_path='/ws/client')
        readonly = types.SimpleNamespace(id='ext-lib', local_path='/ws/ext-lib')
        prepared = PreparedTaskContext(
            branch_name='UNA-1',
            repositories=[writable, readonly],
            repository_branches={'client': 'UNA-1', 'ext-lib': 'UNA-1'},
        )
        self.repository_service.is_branch_pushable.side_effect = (
            lambda repo, branch: repo.id == 'client'
        )

        result = self.service.validate_task_branch_push_access(self.task, prepared)

        self.assertTrue(result)  # continues — not rejected
        self.assertEqual(prepared.read_only_repository_ids, {'ext-lib'})
        # The operator is notified (UI comment) about the read-only repo.
        self.task_service.add_comment.assert_called_once()
        self.assertIn('ext-lib', self.task_service.add_comment.call_args.args[1])

    def test_validate_task_branch_publishability_invokes_failure_handler_when_blocked(self) -> None:
        self.task_branch_publishability_validator.validate.side_effect = RuntimeError('no changes')
        failure_handler = Mock()

        result = self.service.validate_task_branch_publishability(
            self.task,
            self.prepared_task,
            failure_handler=failure_handler,
        )

        self.assertFalse(result)
        failure_handler.assert_called_once()
        self.assertIs(failure_handler.call_args.args[0], self.task)
        self.assertIsInstance(failure_handler.call_args.args[1], RuntimeError)
        self.assertIs(failure_handler.call_args.args[2], self.prepared_task)


class TaskPreflightAttachmentTests(unittest.TestCase):
    """The ticket's screenshots are downloaded into the task folder.

    Reported: "kato fails to pull images from YouTrack — we always need to
    provide them manually." The tracker hands out an attachment as a URL
    needing its own credentials (YouTrack's does not even carry a host), so
    the agent was told a screenshot existed and given no way to open it.

    This runs at preflight because that is the first moment the task HAS a
    folder to put files in.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace_root = Path(self._tmp.name) / 'PROJ-1'
        self.task = build_task()
        self.repository = types.SimpleNamespace(
            id='client',
            local_path=str(self.workspace_root / 'client'),
            destination_branch='main',
        )
        self.task_service = Mock()
        self.task_service.download_image_attachments.return_value = []
        repository_service = Mock()
        repository_service.build_branch_name.return_value = 'feature/proj-1/client'
        self.repository_service = repository_service

    def _service(self, *, workspace_mode: bool = True) -> TaskPreflightService:
        return TaskPreflightService(
            task_model_access_validator=Mock(),
            task_service=self.task_service,
            repository_service=self.repository_service,
            task_branch_push_validator=Mock(),
            task_branch_publishability_validator=Mock(),
            # Only workspace-clone mode gives the task a folder of its own.
            workspace_provisioner=(
                (lambda task, repositories: repositories) if workspace_mode else None
            ),
        )

    def _prepare(self, *, workspace_mode: bool = True) -> PreparedTaskContext:
        return self._service(
            workspace_mode=workspace_mode,
        )._attach_task_repository_context(self.task, [self.repository])

    def test_images_are_downloaded_into_the_tasks_attachments_folder(self) -> None:
        self._prepare()

        self.task_service.download_image_attachments.assert_called_once_with(
            self.task.id, task_attachments_directory(str(self.workspace_root)),
        )

    def test_the_downloaded_paths_ride_on_the_prepared_context(self) -> None:
        # This is what the agent's prompt names, so it must survive preflight.
        written = [str(self.workspace_root / 'attachments' / 'bug.png')]
        self.task_service.download_image_attachments.return_value = written

        self.assertEqual(self._prepare().attachment_paths, written)

    def test_a_ticket_with_no_images_prepares_normally(self) -> None:
        self.assertEqual(self._prepare().attachment_paths, [])

    def test_a_failing_download_never_costs_the_operator_the_task(self) -> None:
        # An image the tracker will not serve is worth some lost context, not
        # a task that refuses to start.
        self.task_service.download_image_attachments.side_effect = RuntimeError('403')

        prepared = self._prepare()

        self.assertEqual(prepared.attachment_paths, [])
        self.assertEqual(prepared.branch_name, 'feature/proj-1/client')

    def test_without_a_task_folder_the_tracker_is_never_asked(self) -> None:
        # No workspace-clone mode means no folder of the task's own; writing
        # into a shared checkout would put files inside a repository.
        prepared = self._prepare(workspace_mode=False)

        self.assertEqual(prepared.attachment_paths, [])
        self.task_service.download_image_attachments.assert_not_called()
