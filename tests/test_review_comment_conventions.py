"""The review-fix commit message follows the repository's own CLAUDE.md /
AGENTS.md, the same way task_publisher.py's does — see
test_publish_conventions_writer.py and test_task_publisher.py's
``TaskPublisherConventionsTests`` for the shared mechanism's own coverage.
This only pins the integration at ``ReviewCommentService``'s call site.
"""

from __future__ import annotations

import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from provider_client_base.provider_client_base.data.review_comment import ReviewComment

from kato_core_lib.data_layers.data.fields import PullRequestFields
from kato_core_lib.data_layers.service.agent_state_registry import AgentStateRegistry
from kato_core_lib.data_layers.service.publish_conventions_writer import (
    PublishConventionsWriter,
    PublishText,
)
from kato_core_lib.data_layers.service.review_comment_service import ReviewCommentService
from tests.review_mention_policy_support import legacy_mention_policy


class ReviewCommentConventionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._mention_policy = legacy_mention_policy()
        self._mention_policy.start()
        self.addCleanup(self._mention_policy.stop)

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo_root = Path(self._tmp.name) / 'client'
        self.repo_root.mkdir()

        self.task_service = types.SimpleNamespace(
            get_review_tasks=Mock(return_value=[]),
            add_comment=Mock(),
        )
        self.implementation_service = types.SimpleNamespace(
            fix_review_comment=Mock(return_value={'success': True}),
        )
        self.repository = types.SimpleNamespace(
            id='client', owner='workspace', repo_slug='repo',
            provider_base_url='https://api.bitbucket.org/2.0',
            local_path=str(self.repo_root),
        )
        self.repository_service = types.SimpleNamespace(
            get_repository=Mock(return_value=self.repository),
            resolve_task_repositories=Mock(return_value=[self.repository]),
            prepare_task_branches=Mock(),
            publish_review_fix=Mock(),
            reply_to_review_comment=Mock(),
            resolve_review_comment=Mock(),
            restore_task_repositories=Mock(),
            build_branch_name=Mock(return_value='PROJ-1'),
            find_pull_requests=Mock(return_value=[]),
            list_pull_request_comments=Mock(return_value=[]),
        )
        self.state_registry = AgentStateRegistry()
        self.state_registry.remember_pull_request_context(
            {
                PullRequestFields.REPOSITORY_ID: 'client',
                PullRequestFields.ID: '17',
                PullRequestFields.TITLE: 'PROJ-1 fix it already',
            },
            'feature/proj-1/client',
            agent_session_id='conversation-1',
            task_id='PROJ-1',
            task_summary='fix it already',
        )
        self.writer = Mock()

    def _service(self, *, writer=None) -> ReviewCommentService:
        return ReviewCommentService(
            self.task_service,
            self.implementation_service,
            self.repository_service,
            self.state_registry,
            publish_conventions_writer=writer,
        )

    def _comment(self) -> ReviewComment:
        return ReviewComment(
            pull_request_id='17', comment_id='99', author='reviewer',
            body='Please rename this variable.',
        )

    def _fix(self, service, *, enabled: bool) -> None:
        with patch(
            'kato_core_lib.data_layers.service.review_comment_service.publish_follows_project_conventions',
            return_value=enabled,
        ):
            service.process_review_comment(self._comment())

    def test_the_writer_is_asked_when_on_and_the_repo_has_conventions(self) -> None:
        (self.repo_root / 'CLAUDE.md').write_text('Commits follow Conventional Commits.', encoding='utf-8')
        self.writer.write.return_value = PublishText(commit_message='fix: rename variable')
        self._fix(self._service(writer=self.writer), enabled=True)

        self.writer.write.assert_called_once()
        self.assertIn(
            'Commits follow Conventional Commits.',
            self.writer.write.call_args.kwargs['conventions_text'],
        )
        self.assertIn(
            'Please rename this variable.',
            self.writer.write.call_args.kwargs['context_text'],
        )
        self.repository_service.publish_review_fix.assert_called_once_with(
            self.repository, 'feature/proj-1/client', 'fix: rename variable',
        )

    def test_the_setting_off_never_asks(self) -> None:
        (self.repo_root / 'CLAUDE.md').write_text('Commits follow Conventional Commits.', encoding='utf-8')
        self._fix(self._service(writer=self.writer), enabled=False)

        self.writer.write.assert_not_called()
        self.repository_service.publish_review_fix.assert_called_once_with(
            self.repository, 'feature/proj-1/client', 'Address review comments',
        )

    def test_no_conventions_file_never_asks(self) -> None:
        self._fix(self._service(writer=self.writer), enabled=True)

        self.writer.write.assert_not_called()
        self.repository_service.publish_review_fix.assert_called_once_with(
            self.repository, 'feature/proj-1/client', 'Address review comments',
        )

    def test_no_writer_wired_behaves_exactly_as_before(self) -> None:
        (self.repo_root / 'CLAUDE.md').write_text('Commits follow Conventional Commits.', encoding='utf-8')
        self._fix(self._service(writer=None), enabled=True)

        self.repository_service.publish_review_fix.assert_called_once_with(
            self.repository, 'feature/proj-1/client', 'Address review comments',
        )

    def test_a_failing_one_shot_still_pushes_with_the_plain_commit_message(self) -> None:
        (self.repo_root / 'CLAUDE.md').write_text('Commits follow Conventional Commits.', encoding='utf-8')

        def boom(prompt: str) -> str:
            raise RuntimeError('model call failed')

        real_writer = PublishConventionsWriter(one_shot=boom, logger=Mock())
        self._fix(self._service(writer=real_writer), enabled=True)

        self.repository_service.publish_review_fix.assert_called_once_with(
            self.repository, 'feature/proj-1/client', 'Address review comments',
        )


if __name__ == '__main__':
    unittest.main()
