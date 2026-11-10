"""Push and on-demand PR creation follow the repository's own CLAUDE.md /
AGENTS.md — the same mechanism as task_publisher.py's autonomous flow (see
test_publish_conventions_writer.py for the shared writer's own coverage).
This only pins the integration at TaskPublishService's two call sites:
``push_task`` (commit message only) and ``create_pull_request_for_task``
(title, description and commit message together).
"""

from __future__ import annotations

import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import ANY, MagicMock, Mock, patch

from kato_core_lib.data_layers.service.publish_conventions_writer import (
    PublishConventionsWriter,
    PublishText,
)
from kato_core_lib.data_layers.service.task_publish_service import TaskPublishService

_GATE = 'kato_core_lib.data_layers.service.task_publish_service.publish_follows_project_conventions'


class _ConventionsFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.clone_root = Path(self._tmp.name) / 'client'
        self.clone_root.mkdir()

        self.repository = types.SimpleNamespace(id='client', destination_branch='master')
        self.repository_service = MagicMock()
        self.repository_service.get_repository.return_value = self.repository
        self.repository_service.build_branch_name.return_value = 'feature/UNA-1'
        self.repository_service.find_pull_requests.return_value = []
        self.repository_service.create_pull_request.return_value = {'url': 'https://host/pr/1'}

        self.workspace_manager = MagicMock()
        self.workspace_manager.get.return_value = types.SimpleNamespace(
            task_id='UNA-1', task_summary='Fix the thing', repository_ids=['client'],
        )
        self.workspace_manager.repository_path.return_value = self.clone_root

        self.writer = Mock()

    def _service(self, *, writer=None) -> TaskPublishService:
        return TaskPublishService(
            repository_service=self.repository_service,
            task_service=MagicMock(),
            task_state_service=MagicMock(),
            task_publisher=MagicMock(),
            workspace_manager=self.workspace_manager,
            logger=Mock(),
            publish_conventions_writer=writer,
        )

    def _write_claude_md(self, text: str) -> None:
        (self.clone_root / 'CLAUDE.md').write_text(text, encoding='utf-8')


class PushTaskConventionsTests(_ConventionsFixture):
    def setUp(self) -> None:
        super().setUp()
        # Not "nothing to push" — the push-skip pre-check must say so, or
        # push_task skips the repository before the commit message is ever
        # built.
        self.repository_service.push_skip_reason.return_value = ''

    def _push(self, service, *, enabled: bool):
        with patch(_GATE, return_value=enabled):
            return service.push_task('UNA-1')

    def test_the_writer_is_asked_when_on_and_the_repo_has_conventions(self) -> None:
        self._write_claude_md('Commits follow Conventional Commits.')
        self.writer.write.return_value = PublishText(commit_message='feat: push update')
        self._push(self._service(writer=self.writer), enabled=True)

        self.writer.write.assert_called_once()
        self.assertIn(
            'Commits follow Conventional Commits.',
            self.writer.write.call_args.kwargs['conventions_text'],
        )
        self.repository_service.publish_review_fix.assert_called_once_with(
            ANY, 'feature/UNA-1', commit_message='feat: push update',
        )

    def test_the_setting_off_never_asks(self) -> None:
        self._write_claude_md('Commits follow Conventional Commits.')
        self._push(self._service(writer=self.writer), enabled=False)

        self.writer.write.assert_not_called()
        self.repository_service.publish_review_fix.assert_called_once_with(
            ANY, 'feature/UNA-1', commit_message='Update UNA-1',
        )

    def test_no_conventions_file_never_asks(self) -> None:
        self._push(self._service(writer=self.writer), enabled=True)

        self.writer.write.assert_not_called()
        self.repository_service.publish_review_fix.assert_called_once_with(
            ANY, 'feature/UNA-1', commit_message='Update UNA-1',
        )

    def test_no_writer_wired_behaves_exactly_as_before(self) -> None:
        self._write_claude_md('Commits follow Conventional Commits.')
        self._push(self._service(writer=None), enabled=True)

        self.repository_service.publish_review_fix.assert_called_once_with(
            ANY, 'feature/UNA-1', commit_message='Update UNA-1',
        )

    def test_a_failing_one_shot_still_pushes_with_the_plain_commit_message(self) -> None:
        self._write_claude_md('Commits follow Conventional Commits.')

        def boom(prompt: str) -> str:
            raise RuntimeError('model call failed')

        real_writer = PublishConventionsWriter(one_shot=boom, logger=Mock())
        self._push(self._service(writer=real_writer), enabled=True)

        self.repository_service.publish_review_fix.assert_called_once_with(
            ANY, 'feature/UNA-1', commit_message='Update UNA-1',
        )


class CreatePullRequestForTaskConventionsTests(_ConventionsFixture):
    def _create(self, service, *, enabled: bool):
        with patch(_GATE, return_value=enabled):
            return service.create_pull_request_for_task('UNA-1')

    def test_the_writer_is_asked_for_title_description_and_commit_message(self) -> None:
        self._write_claude_md('PRs need a Summary and a Test plan section.')
        self.writer.write.return_value = PublishText(
            commit_message='feat: x', title='UNA-1 Conventioned', description='## Summary\n...',
        )
        self._create(self._service(writer=self.writer), enabled=True)

        self.writer.write.assert_called_once()
        kwargs = self.writer.write.call_args.kwargs
        self.assertEqual(kwargs['fallback_title'], 'UNA-1 Fix the thing')
        self.assertEqual(kwargs['fallback_description'], 'Fix the thing')
        self.assertEqual(kwargs['fallback_commit_message'], 'UNA-1 Fix the thing')
        self.repository_service.create_pull_request.assert_called_once_with(
            ANY,
            title='UNA-1 Conventioned',
            source_branch='feature/UNA-1',
            description='## Summary\n...',
            commit_message='feat: x',
        )

    def test_the_setting_off_keeps_todays_plain_text(self) -> None:
        self._write_claude_md('PRs need a Summary and a Test plan section.')
        self._create(self._service(writer=self.writer), enabled=False)

        self.writer.write.assert_not_called()
        self.repository_service.create_pull_request.assert_called_once_with(
            ANY,
            title='UNA-1 Fix the thing',
            source_branch='feature/UNA-1',
            description='Fix the thing',
            commit_message='UNA-1 Fix the thing',
        )

    def test_no_conventions_file_never_asks(self) -> None:
        self._create(self._service(writer=self.writer), enabled=True)
        self.writer.write.assert_not_called()


if __name__ == '__main__':
    unittest.main()
