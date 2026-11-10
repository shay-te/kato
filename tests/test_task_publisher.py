import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from kato_core_lib.data_layers.data.fields import (
    ImplementationFields,
    PullRequestFields,
    StatusFields,
)
from kato_core_lib.data_layers.data.task import Task
from kato_core_lib.data_layers.service.agent_state_registry import AgentStateRegistry
from kato_core_lib.data_layers.service.notification_service import NotificationService
from kato_core_lib.data_layers.service.publish_conventions_writer import PublishText
from kato_core_lib.data_layers.service.repository_service import (
    RepositoryHasNoChangesError,
    RepositoryService,
)
from kato_core_lib.data_layers.service.task_failure_handler import TaskFailureHandler
from kato_core_lib.data_layers.service.task_state_service import TaskStateService
from kato_core_lib.data_layers.service.task_publisher import TaskPublisher
from kato_core_lib.data_layers.service.task_service import TaskService
from tests.utils import build_task


class TaskPublisherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.task_service = Mock(spec=TaskService)
        self.task_service.add_comment = Mock()
        self.task_state_service = Mock(spec=TaskStateService)
        self.task_state_service.move_task_to_review = Mock()
        self.repository_service = Mock(spec=RepositoryService)
        self.notification_service = Mock(spec=NotificationService)
        self.state_registry = Mock(spec=AgentStateRegistry)
        self.failure_handler = Mock(spec=TaskFailureHandler)
        self.publisher = TaskPublisher(
            self.task_service,
            self.task_state_service,
            self.repository_service,
            self.notification_service,
            self.state_registry,
            self.failure_handler,
            # Most tests don't exercise the retry loop; force a single
            # attempt so a single side-effect entry per repo is enough.
            # Tests that *do* exercise retries instantiate their own
            # publisher with the desired budget.
            publish_max_retries=0,
        )

    def test_publish_task_execution_marks_processed_and_moves_to_review(self) -> None:
        task = build_task(description='whats wrong with you please fix it')
        prepared_task = types.SimpleNamespace(
            repositories=[
                types.SimpleNamespace(id='client', destination_branch='master'),
                types.SimpleNamespace(id='backend', destination_branch='main'),
            ],
            repository_branches={
                'client': 'feature/proj-1/client',
                'backend': 'feature/proj-1/backend',
            },
        )
        execution = {
            ImplementationFields.SUCCESS: True,
            ImplementationFields.AGENT_SESSION_ID: ' conversation-1\n',
            Task.summary.key: 'Files changed:\n- client/app.ts\n  Updated the client flow.',
            ImplementationFields.MESSAGE: 'Validation report:\n- verified the implementation.',
        }
        self.repository_service.create_pull_request.side_effect = [
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                PullRequestFields.ID: '17',
                PullRequestFields.TITLE: 'PROJ-1: fix it already',
                PullRequestFields.URL: 'https://bitbucket/pr/17',
                PullRequestFields.SOURCE_BRANCH: 'feature/proj-1/client',
                PullRequestFields.DESTINATION_BRANCH: 'master',
            },
            {
                PullRequestFields.REPOSITORY_ID: 'backend',
                PullRequestFields.ID: '18',
                PullRequestFields.TITLE: 'PROJ-1: fix it already',
                PullRequestFields.URL: 'https://github/pr/18',
                PullRequestFields.SOURCE_BRANCH: 'feature/proj-1/backend',
                PullRequestFields.DESTINATION_BRANCH: 'main',
            },
        ]

        result = self.publisher.publish_task_execution(task, prepared_task, execution)

        self.assertEqual(result[StatusFields.STATUS], StatusFields.READY_FOR_REVIEW)
        self.task_state_service.move_task_to_review.assert_called_once_with(task.id)
        self.state_registry.mark_task_processed.assert_called_once()
        processed_args = self.state_registry.mark_task_processed.call_args.args
        self.assertEqual(processed_args[0], task.id)
        self.assertEqual(
            [pull_request[PullRequestFields.REPOSITORY_ID] for pull_request in processed_args[1]],
            ['client', 'backend'],
        )
        self.notification_service.notify_task_ready_for_review.assert_called_once()
        self.assertEqual(self.repository_service.create_pull_request.call_count, 2)
        first_call = self.repository_service.create_pull_request.call_args_list[0]
        self.assertEqual(first_call.kwargs['title'], 'PROJ-1 fix it already')
        self.assertIn('Requested change:', first_call.kwargs['description'])
        self.assertIn('Implementation summary:', first_call.kwargs['description'])
        self.assertIn('Execution notes:', first_call.kwargs['description'])
        self.assertEqual(self.task_service.add_comment.call_count, 1)
        self.assertIn(
            'Published review links:',
            self.task_service.add_comment.call_args.args[1],
        )
        self.state_registry.remember_pull_request_context.assert_called()
        first_context_call = (
            self.state_registry.remember_pull_request_context.call_args_list[0]
        )
        self.assertEqual(first_context_call.args[2], 'conversation-1')

    def test_publish_task_execution_skips_read_only_repository(self) -> None:
        # A repo kato can't push to (marked read-only by the preflight) is never
        # PR'd — only the writable repo gets a pull request, and the task still
        # publishes. One un-pushable reference repo no longer blocks the task.
        task = build_task(description='whats wrong with you please fix it')
        prepared_task = types.SimpleNamespace(
            repositories=[
                types.SimpleNamespace(id='client', destination_branch='master'),
                types.SimpleNamespace(id='ext-lib', destination_branch='main'),
            ],
            repository_branches={'client': 'UNA-1', 'ext-lib': 'UNA-1'},
            read_only_repository_ids={'ext-lib'},
        )
        execution = {
            ImplementationFields.SUCCESS: True,
            ImplementationFields.AGENT_SESSION_ID: 'conversation-1',
            Task.summary.key: 'Files changed:\n- client/app.ts',
            ImplementationFields.MESSAGE: 'Validation report:\n- verified.',
        }
        self.repository_service.create_pull_request.side_effect = [{
            PullRequestFields.REPOSITORY_ID: 'client',
            PullRequestFields.ID: '17',
            PullRequestFields.TITLE: 'PROJ-1: fix',
            PullRequestFields.URL: 'https://bitbucket/pr/17',
            PullRequestFields.SOURCE_BRANCH: 'UNA-1',
            PullRequestFields.DESTINATION_BRANCH: 'master',
        }]

        result = self.publisher.publish_task_execution(task, prepared_task, execution)

        # Only the writable repo was PR'd; the read-only one was skipped.
        self.assertEqual(self.repository_service.create_pull_request.call_count, 1)
        self.assertEqual(result[StatusFields.STATUS], StatusFields.READY_FOR_REVIEW)

    def test_publish_task_execution_partial_failure_reports_failure(self) -> None:
        task = build_task(description='whats wrong with you please fix it')
        prepared_task = types.SimpleNamespace(
            repositories=[
                types.SimpleNamespace(id='client', destination_branch='master'),
                types.SimpleNamespace(id='backend', destination_branch='main'),
            ],
            repository_branches={
                'client': 'feature/proj-1/client',
                'backend': 'feature/proj-1/backend',
            },
        )
        execution = {
            ImplementationFields.SUCCESS: True,
            ImplementationFields.AGENT_SESSION_ID: 'conversation-1',
            Task.summary.key: 'Files changed:\n- client/app.ts',
            ImplementationFields.MESSAGE: 'Validation report:\n- verified the implementation.',
        }
        self.repository_service.create_pull_request.side_effect = [
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                PullRequestFields.ID: '17',
                PullRequestFields.TITLE: 'PROJ-1: fix it already',
                PullRequestFields.URL: 'https://bitbucket/pr/17',
                PullRequestFields.SOURCE_BRANCH: 'feature/proj-1/client',
                PullRequestFields.DESTINATION_BRANCH: 'master',
            },
            RuntimeError('github down'),
        ]

        result = self.publisher.publish_task_execution(task, prepared_task, execution)

        self.assertEqual(result[StatusFields.STATUS], StatusFields.PARTIAL_FAILURE)
        self.assertEqual(
            result[PullRequestFields.FAILED_REPOSITORIES],
            [{
                PullRequestFields.REPOSITORY_ID: 'backend',
                'error': 'github down',
            }],
        )
        self.failure_handler.handle_started_task_failure.assert_called_once()
        failure_args, failure_kwargs = self.failure_handler.handle_started_task_failure.call_args
        self.assertEqual(failure_args[0], task)
        # Failure message now carries the per-repo reason so the
        # operator can act on it without spelunking through logs.
        self.assertEqual(
            str(failure_args[1]),
            'failed to create pull requests for repositories: backend (github down)',
        )
        self.assertEqual(failure_kwargs['prepared_task'], prepared_task)

    def test_publish_task_execution_failure_to_move_review_calls_failure_handler(self) -> None:
        task = build_task(description='whats wrong with you please fix it')
        prepared_task = types.SimpleNamespace(
            repositories=[types.SimpleNamespace(id='client', destination_branch='master')],
            repository_branches={'client': 'feature/proj-1/client'},
        )
        execution = {
            ImplementationFields.SUCCESS: True,
            ImplementationFields.AGENT_SESSION_ID: 'conversation-1',
            Task.summary.key: 'Files changed:\n- client/app.ts',
            ImplementationFields.MESSAGE: 'Validation report:\n- verified the implementation.',
        }
        self.repository_service.create_pull_request.return_value = {
            PullRequestFields.REPOSITORY_ID: 'client',
            PullRequestFields.ID: '17',
            PullRequestFields.TITLE: 'PROJ-1: fix it already',
            PullRequestFields.URL: 'https://bitbucket/pr/17',
            PullRequestFields.SOURCE_BRANCH: 'feature/proj-1/client',
            PullRequestFields.DESTINATION_BRANCH: 'master',
        }
        self.task_state_service.move_task_to_review.side_effect = RuntimeError('transition failed')

        result = self.publisher.publish_task_execution(task, prepared_task, execution)

        self.assertIsNone(result)
        self.failure_handler.handle_started_task_failure.assert_called_once()
        failure_args, failure_kwargs = self.failure_handler.handle_started_task_failure.call_args
        self.assertEqual(failure_args[0], task)
        self.assertEqual(str(failure_args[1]), 'transition failed')
        self.assertEqual(failure_kwargs['prepared_task'], prepared_task)
        self.state_registry.mark_task_processed.assert_not_called()
        self.notification_service.notify_task_ready_for_review.assert_not_called()

    def test_comment_task_started_uses_repository_context(self) -> None:
        task = build_task(description='whats wrong with you please fix it')

        self.publisher.comment_task_started(
            task,
            [types.SimpleNamespace(id='client'), types.SimpleNamespace(id='backend')],
        )

        self.task_service.add_comment.assert_called_once()
        comment = self.task_service.add_comment.call_args.args[1]
        self.assertIn('started working on this task in repositories: client, backend', comment)

    def test_publish_task_execution_treats_no_changes_repo_as_success_skip(self) -> None:
        # Real-world shape of UNA-2574: three repos tagged, the agent
        # only edited two, the third (tagged for context) had nothing
        # to publish. Should land on the review state — not partial
        # failure — and the unchanged repo should be listed in the
        # summary comment so the reviewer knows it was deliberately
        # skipped, not silently forgotten.
        task = build_task(description='Multi-repo task with one context-only repo')
        prepared_task = types.SimpleNamespace(
            repositories=[
                types.SimpleNamespace(id='client', destination_branch='master'),
                types.SimpleNamespace(id='shared', destination_branch='master'),
                types.SimpleNamespace(id='backend', destination_branch='main'),
            ],
            repository_branches={
                'client': 'feature/proj-1/client',
                'shared': 'feature/proj-1/shared',
                'backend': 'feature/proj-1/backend',
            },
        )
        execution = {
            ImplementationFields.SUCCESS: True,
            ImplementationFields.AGENT_SESSION_ID: 'conversation-1',
            ImplementationFields.MESSAGE: 'Validation report:\n- looked at shared.',
        }
        self.repository_service.create_pull_request.side_effect = [
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                PullRequestFields.ID: '17',
                PullRequestFields.TITLE: 'PROJ-1: fix it already',
                PullRequestFields.URL: 'https://bitbucket/pr/17',
                PullRequestFields.SOURCE_BRANCH: 'feature/proj-1/client',
                PullRequestFields.DESTINATION_BRANCH: 'master',
            },
            RepositoryHasNoChangesError(
                'branch feature/proj-1/shared has no task changes ahead of origin/master'
            ),
            {
                PullRequestFields.REPOSITORY_ID: 'backend',
                PullRequestFields.ID: '18',
                PullRequestFields.TITLE: 'PROJ-1: fix it already',
                PullRequestFields.URL: 'https://github/pr/18',
                PullRequestFields.SOURCE_BRANCH: 'feature/proj-1/backend',
                PullRequestFields.DESTINATION_BRANCH: 'main',
            },
        ]

        result = self.publisher.publish_task_execution(task, prepared_task, execution)

        # No-op repos do NOT count as failures — task moves to review.
        self.assertEqual(result[StatusFields.STATUS], StatusFields.READY_FOR_REVIEW)
        self.assertEqual(result[PullRequestFields.FAILED_REPOSITORIES], [])
        self.task_state_service.move_task_to_review.assert_called_once_with(task.id)
        self.failure_handler.handle_started_task_failure.assert_not_called()
        self.notification_service.notify_task_ready_for_review.assert_called_once()
        # Only the two repos with real changes carry pull requests.
        published_repo_ids = [
            pr[PullRequestFields.REPOSITORY_ID]
            for pr in result[PullRequestFields.PULL_REQUESTS]
        ]
        self.assertEqual(published_repo_ids, ['client', 'backend'])
        # The summary comment mentions the unchanged repo so the
        # reviewer can see it was intentionally skipped.
        self.task_service.add_comment.assert_called_once()
        comment_text = self.task_service.add_comment.call_args.args[1]
        self.assertIn('No changes were needed in: shared', comment_text)

    def test_publish_task_execution_all_repos_unchanged_skips_publish(self) -> None:
        # Edge case: every tagged repo was context-only (or the agent
        # didn't make any edits). With no PR opened and no push, the
        # task must NOT move to "In Review" — that would falsely claim
        # success. Instead the publisher surfaces a NO_CHANGES failure
        # so the task stays in its current state and the operator can
        # see that nothing was actually published.
        task = build_task(description='Multi-repo task with no edits anywhere')
        prepared_task = types.SimpleNamespace(
            repositories=[
                types.SimpleNamespace(id='client', destination_branch='master'),
                types.SimpleNamespace(id='shared', destination_branch='master'),
            ],
            repository_branches={
                'client': 'feature/proj-1/client',
                'shared': 'feature/proj-1/shared',
            },
        )
        execution = {
            ImplementationFields.SUCCESS: True,
            ImplementationFields.AGENT_SESSION_ID: 'conversation-1',
        }
        self.repository_service.create_pull_request.side_effect = [
            RepositoryHasNoChangesError('branch X has no task changes ahead of master'),
            RepositoryHasNoChangesError('branch Y has no task changes ahead of master'),
        ]

        result = self.publisher.publish_task_execution(task, prepared_task, execution)

        self.assertEqual(result[StatusFields.STATUS], StatusFields.NO_CHANGES)
        self.assertEqual(result[PullRequestFields.PULL_REQUESTS], [])
        self.assertEqual(result[PullRequestFields.FAILED_REPOSITORIES], [])
        self.failure_handler.handle_started_task_failure.assert_called_once()
        self.task_state_service.move_task_to_review.assert_not_called()

    def test_publish_task_execution_retries_pr_creation_on_transient_failure(self) -> None:
        # Two PR-creation calls fail before the third succeeds. With
        # the default 2-retry budget the publisher should keep going,
        # the task should land on review, and no failure handler call.
        sleep_calls: list[float] = []
        publisher = TaskPublisher(
            self.task_service,
            self.task_state_service,
            self.repository_service,
            self.notification_service,
            self.state_registry,
            self.failure_handler,
            publish_max_retries=2,
            sleep_fn=sleep_calls.append,
        )
        task = build_task(description='Single repo, transient publish flakes')
        prepared_task = types.SimpleNamespace(
            repositories=[types.SimpleNamespace(id='client', destination_branch='master')],
            repository_branches={'client': 'feature/proj-1/client'},
        )
        execution = {ImplementationFields.SUCCESS: True}
        successful_pr = {
            PullRequestFields.REPOSITORY_ID: 'client',
            PullRequestFields.ID: '17',
            PullRequestFields.TITLE: 'PROJ-1: fix it already',
            PullRequestFields.URL: 'https://bitbucket/pr/17',
            PullRequestFields.SOURCE_BRANCH: 'feature/proj-1/client',
            PullRequestFields.DESTINATION_BRANCH: 'master',
        }
        self.repository_service.create_pull_request.side_effect = [
            RuntimeError('git push timed out'),
            RuntimeError('bitbucket 502'),
            successful_pr,
        ]

        result = publisher.publish_task_execution(task, prepared_task, execution)

        self.assertEqual(result[StatusFields.STATUS], StatusFields.READY_FOR_REVIEW)
        self.assertEqual(self.repository_service.create_pull_request.call_count, 3)
        # Two retries → two backoff sleeps (1s, 2s).
        self.assertEqual(len(sleep_calls), 2)
        self.assertEqual(sleep_calls[0], 1.0)
        self.assertEqual(sleep_calls[1], 2.0)
        self.failure_handler.handle_started_task_failure.assert_not_called()

    def test_publish_task_execution_marks_repo_failed_after_exhausting_retries(self) -> None:
        # Every retry attempt blows up. Once the budget is used up,
        # the repo lands in failed_repositories and we drop into the
        # existing partial-failure path.
        publisher = TaskPublisher(
            self.task_service,
            self.task_state_service,
            self.repository_service,
            self.notification_service,
            self.state_registry,
            self.failure_handler,
            publish_max_retries=2,
            sleep_fn=lambda _: None,
        )
        task = build_task(description='Single repo permanent failure')
        prepared_task = types.SimpleNamespace(
            repositories=[types.SimpleNamespace(id='client', destination_branch='master')],
            repository_branches={'client': 'feature/proj-1/client'},
        )
        execution = {ImplementationFields.SUCCESS: True}
        self.repository_service.create_pull_request.side_effect = RuntimeError(
            'permanent bitbucket outage',
        )

        result = publisher.publish_task_execution(task, prepared_task, execution)

        # 1 initial attempt + 2 retries = 3 total calls.
        self.assertEqual(self.repository_service.create_pull_request.call_count, 3)
        self.assertEqual(result[StatusFields.STATUS], StatusFields.PARTIAL_FAILURE)
        self.assertEqual(
            result[PullRequestFields.FAILED_REPOSITORIES],
            [{
                PullRequestFields.REPOSITORY_ID: 'client',
                'error': 'permanent bitbucket outage',
            }],
        )

    def test_publish_task_execution_does_not_retry_no_changes_error(self) -> None:
        # The "branch has no task changes" outcome is deterministic;
        # retrying is pointless. Verify the retry helper short-circuits.
        publisher = TaskPublisher(
            self.task_service,
            self.task_state_service,
            self.repository_service,
            self.notification_service,
            self.state_registry,
            self.failure_handler,
            publish_max_retries=5,  # extra-generous to make the bug obvious if regressed
            sleep_fn=lambda _: None,
        )
        task = build_task(description='Single repo no-changes')
        prepared_task = types.SimpleNamespace(
            repositories=[types.SimpleNamespace(id='client', destination_branch='master')],
            repository_branches={'client': 'feature/proj-1/client'},
        )
        execution = {ImplementationFields.SUCCESS: True}
        self.repository_service.create_pull_request.side_effect = (
            RepositoryHasNoChangesError('branch X has no task changes ahead of master')
        )

        result = publisher.publish_task_execution(task, prepared_task, execution)

        # Exactly one call — no retries on the no-changes path.
        self.assertEqual(self.repository_service.create_pull_request.call_count, 1)
        # No push happened → task must NOT move to review.
        self.assertEqual(result[StatusFields.STATUS], StatusFields.NO_CHANGES)
        self.task_state_service.move_task_to_review.assert_not_called()

    def test_publish_task_execution_retries_move_to_review(self) -> None:
        # PRs created cleanly, but the YouTrack/Jira state transition
        # blips. Retry budget covers this so a flaky ticket platform
        # doesn't kill an otherwise-finished task.
        sleep_calls: list[float] = []
        publisher = TaskPublisher(
            self.task_service,
            self.task_state_service,
            self.repository_service,
            self.notification_service,
            self.state_registry,
            self.failure_handler,
            publish_max_retries=2,
            sleep_fn=sleep_calls.append,
        )
        task = build_task(description='Move-to-review flake')
        prepared_task = types.SimpleNamespace(
            repositories=[types.SimpleNamespace(id='client', destination_branch='master')],
            repository_branches={'client': 'feature/proj-1/client'},
        )
        execution = {ImplementationFields.SUCCESS: True}
        self.repository_service.create_pull_request.return_value = {
            PullRequestFields.REPOSITORY_ID: 'client',
            PullRequestFields.ID: '17',
            PullRequestFields.TITLE: 'PROJ-1: fix it already',
            PullRequestFields.URL: 'https://bitbucket/pr/17',
            PullRequestFields.SOURCE_BRANCH: 'feature/proj-1/client',
            PullRequestFields.DESTINATION_BRANCH: 'master',
        }
        self.task_state_service.move_task_to_review.side_effect = [
            RuntimeError('youtrack 502'),
            None,  # second attempt succeeds
        ]

        result = publisher.publish_task_execution(task, prepared_task, execution)

        self.assertEqual(self.task_state_service.move_task_to_review.call_count, 2)
        self.assertEqual(result[StatusFields.STATUS], StatusFields.READY_FOR_REVIEW)
        self.failure_handler.handle_started_task_failure.assert_not_called()
        self.assertEqual(len(sleep_calls), 1)
        self.assertEqual(sleep_calls[0], 1.0)

    def test_publish_task_execution_mixes_unchanged_and_failed_repos(self) -> None:
        # Tighten the contract: the no-op skip is *independent* of
        # genuine failures. If one repo is a clean no-op AND another
        # blows up, the task is still partial-failure (failed list
        # populated, unchanged list noted, run_to_review stays put).
        task = build_task(description='Mixed outcome multi-repo task')
        prepared_task = types.SimpleNamespace(
            repositories=[
                types.SimpleNamespace(id='client', destination_branch='master'),
                types.SimpleNamespace(id='shared', destination_branch='master'),
                types.SimpleNamespace(id='backend', destination_branch='main'),
            ],
            repository_branches={
                'client': 'feature/proj-1/client',
                'shared': 'feature/proj-1/shared',
                'backend': 'feature/proj-1/backend',
            },
        )
        execution = {
            ImplementationFields.SUCCESS: True,
            ImplementationFields.AGENT_SESSION_ID: 'conversation-1',
        }
        self.repository_service.create_pull_request.side_effect = [
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                PullRequestFields.ID: '17',
                PullRequestFields.TITLE: 'PROJ-1: fix it already',
                PullRequestFields.URL: 'https://bitbucket/pr/17',
                PullRequestFields.SOURCE_BRANCH: 'feature/proj-1/client',
                PullRequestFields.DESTINATION_BRANCH: 'master',
            },
            RepositoryHasNoChangesError('branch X has no task changes ahead of master'),
            RuntimeError('github down'),
        ]

        result = self.publisher.publish_task_execution(task, prepared_task, execution)

        self.assertEqual(result[StatusFields.STATUS], StatusFields.PARTIAL_FAILURE)
        self.assertEqual(
            result[PullRequestFields.FAILED_REPOSITORIES],
            [{
                PullRequestFields.REPOSITORY_ID: 'backend',
                'error': 'github down',
            }],
        )
        # Unchanged repo isn't in the failed list and isn't in the PRs.
        published_repo_ids = [
            pr[PullRequestFields.REPOSITORY_ID]
            for pr in result[PullRequestFields.PULL_REQUESTS]
        ]
        self.assertEqual(published_repo_ids, ['client'])
        self.failure_handler.handle_started_task_failure.assert_called_once()


class TaskPublisherConventionsTests(unittest.TestCase):
    """Commit message / PR title / PR description follow a repository's own
    CLAUDE.md / AGENTS.md — only when the operator turns it on AND at least
    one repository actually has one. Otherwise: kato's own plain text,
    unchanged — exactly the existing fallback values asserted above."""

    def setUp(self) -> None:
        self.task_service = Mock(spec=TaskService)
        self.task_state_service = Mock(spec=TaskStateService)
        self.repository_service = Mock(spec=RepositoryService)
        self.repository_service.create_pull_request.return_value = {
            PullRequestFields.REPOSITORY_ID: 'client',
            PullRequestFields.ID: '1',
            PullRequestFields.URL: 'https://host/pr/1',
        }
        self.notification_service = Mock(spec=NotificationService)
        self.state_registry = Mock(spec=AgentStateRegistry)
        self.failure_handler = Mock(spec=TaskFailureHandler)
        self.writer = Mock()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _publisher(self, *, writer=None) -> TaskPublisher:
        return TaskPublisher(
            self.task_service,
            self.task_state_service,
            self.repository_service,
            self.notification_service,
            self.state_registry,
            self.failure_handler,
            publish_max_retries=0,
            publish_conventions_writer=writer,
        )

    def _repo_with_claude_md(self, repo_id: str, text: str) -> types.SimpleNamespace:
        root = Path(self._tmp.name) / repo_id
        root.mkdir()
        (root / 'CLAUDE.md').write_text(text, encoding='utf-8')
        return types.SimpleNamespace(id=repo_id, destination_branch='master', local_path=str(root))

    def _publish(self, publisher, *, repositories, enabled: bool):
        task = build_task(description='fix the thing')
        prepared_task = types.SimpleNamespace(
            repositories=repositories,
            repository_branches={repo.id: f'feature/{repo.id}' for repo in repositories},
        )
        execution = {ImplementationFields.SUCCESS: True}
        with patch(
            'kato_core_lib.data_layers.service.task_publisher.publish_follows_project_conventions',
            return_value=enabled,
        ):
            publisher.publish_task_execution(task, prepared_task, execution)

    def test_the_writer_is_asked_when_on_and_a_repo_has_conventions(self) -> None:
        self.writer.write.return_value = PublishText(
            commit_message='feat: x', title='Conventioned title', description='Conventioned body',
        )
        repo = self._repo_with_claude_md('client', 'Commits follow Conventional Commits.')
        self._publish(self._publisher(writer=self.writer), repositories=[repo], enabled=True)

        self.writer.write.assert_called_once()
        self.assertIn('Commits follow Conventional Commits.', self.writer.write.call_args.kwargs['conventions_text'])
        call = self.repository_service.create_pull_request.call_args
        self.assertEqual(call.kwargs['title'], 'Conventioned title')
        self.assertEqual(call.kwargs['description'], 'Conventioned body')
        self.assertEqual(call.kwargs['commit_message'], 'feat: x')

    def test_one_shared_call_covers_every_repository_of_the_task(self) -> None:
        self.writer.write.return_value = PublishText(commit_message='x', title='T', description='D')
        repos = [
            self._repo_with_claude_md('client', 'rule one'),
            self._repo_with_claude_md('backend', 'rule two'),
        ]
        self._publish(self._publisher(writer=self.writer), repositories=repos, enabled=True)

        self.writer.write.assert_called_once()
        self.assertEqual(self.repository_service.create_pull_request.call_count, 2)
        for call in self.repository_service.create_pull_request.call_args_list:
            self.assertEqual(call.kwargs['title'], 'T')

    def test_the_setting_off_never_asks_even_with_a_claude_md(self) -> None:
        task = build_task(description='fix the thing')
        repo = self._repo_with_claude_md('client', 'Commits follow Conventional Commits.')
        self._publish(self._publisher(writer=self.writer), repositories=[repo], enabled=False)

        self.writer.write.assert_not_called()
        call = self.repository_service.create_pull_request.call_args
        self.assertEqual(call.kwargs['title'], f'{task.id} {task.summary}')
        self.assertEqual(call.kwargs['commit_message'], f'Implement {task.id}')

    def test_no_conventions_file_anywhere_never_asks(self) -> None:
        # Setting on, writer wired — but the repo's folder has neither
        # CLAUDE.md nor AGENTS.md, so there is nothing to follow.
        root = Path(self._tmp.name) / 'bare'
        root.mkdir()
        repo = types.SimpleNamespace(id='bare', destination_branch='master', local_path=str(root))
        self._publish(self._publisher(writer=self.writer), repositories=[repo], enabled=True)

        self.writer.write.assert_not_called()

    def test_no_writer_wired_at_all_never_asks_even_if_on(self) -> None:
        repo = self._repo_with_claude_md('client', 'Commits follow Conventional Commits.')
        self._publish(self._publisher(writer=None), repositories=[repo], enabled=True)
        # Nothing to assert on self.writer (never injected) — the plain
        # assertion is simply that this doesn't raise and still publishes.
        self.repository_service.create_pull_request.assert_called_once()

    def test_a_failing_one_shot_still_publishes_with_kato_s_own_text(self) -> None:
        # The REAL writer (not a mock): proves the integration relies on its
        # own "any failure means the fallback" contract rather than a second
        # guard here — publish_conventions_writer.py has the unit coverage
        # for every way the one-shot call itself can fail.
        from kato_core_lib.data_layers.service.publish_conventions_writer import (
            PublishConventionsWriter,
        )

        def boom(prompt: str) -> str:
            raise RuntimeError('model call failed')

        task = build_task(description='fix the thing')
        real_writer = PublishConventionsWriter(one_shot=boom, logger=Mock())
        repo = self._repo_with_claude_md('client', 'Commits follow Conventional Commits.')
        self._publish(self._publisher(writer=real_writer), repositories=[repo], enabled=True)

        call = self.repository_service.create_pull_request.call_args
        self.assertEqual(call.kwargs['title'], f'{task.id} {task.summary}')
        self.assertEqual(call.kwargs['commit_message'], f'Implement {task.id}')
