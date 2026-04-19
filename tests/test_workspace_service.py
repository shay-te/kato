from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import unittest

from kato.data_layers.service.workspace_service import WorkspaceService


class WorkspaceServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository_config = {
            'client': {'git_url': 'git@github.com:acme/client.git', 'branch': 'main'},
            'backend': {'git_url': 'git@github.com:acme/backend.git', 'branch': 'develop'},
            'secret': {'git_url': 'git@github.com:acme/secret.git', 'branch': 'main'},
        }

    def test_create_workspace_clones_selected_repositories_in_parallel(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_service = WorkspaceService(
                workspace_base_path=temp_dir,
                repository_config=self.repository_config,
                secret_projects=['secret'],
                max_parallel_clones=5,
            )
            result_calls: list[list[str]] = []

            def fake_run(command, capture_output, text):
                result_calls.append(command)
                return Mock(returncode=0, stderr='')

            with patch(
                'kato.data_layers.service.workspace_service.subprocess.run',
                side_effect=fake_run,
            ) as mock_run:
                workspace_path = workspace_service.create_workspace(
                    'PROJ-1',
                    ['client', 'backend', 'secret'],
                )

            self.assertEqual(workspace_path, Path(temp_dir) / 'PROJ-1')
            self.assertTrue(workspace_path.exists())
            self.assertEqual(mock_run.call_count, 2)
            cloned_targets = {Path(command[-1]).name for command in result_calls}
            self.assertEqual(cloned_targets, {'client', 'backend'})

    def test_create_workspace_raises_for_unknown_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_service = WorkspaceService(
                workspace_base_path=temp_dir,
                repository_config=self.repository_config,
                secret_projects=[],
            )

            with self.assertRaisesRegex(ValueError, "project 'missing' not found"):
                workspace_service.create_workspace('PROJ-1', ['missing'])

    def test_create_workspace_deletes_workspace_when_clone_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_service = WorkspaceService(
                workspace_base_path=temp_dir,
                repository_config=self.repository_config,
                secret_projects=[],
            )

            def fake_run(command, capture_output, text):
                if command[-1].endswith('backend'):
                    return Mock(returncode=1, stderr='clone failed')
                return Mock(returncode=0, stderr='')

            with patch(
                'kato.data_layers.service.workspace_service.subprocess.run',
                side_effect=fake_run,
            ):
                with self.assertRaisesRegex(RuntimeError, 'failed to clone required repositories'):
                    workspace_service.create_workspace('PROJ-1', ['client', 'backend'])

            self.assertFalse((Path(temp_dir) / 'PROJ-1').exists())

    def test_cleanup_workspace_returns_false_when_workspace_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_service = WorkspaceService(
                workspace_base_path=temp_dir,
                repository_config=self.repository_config,
                secret_projects=[],
            )

            self.assertFalse(workspace_service.cleanup_workspace('PROJ-1'))

    def test_workspace_exists_tracks_created_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_service = WorkspaceService(
                workspace_base_path=temp_dir,
                repository_config=self.repository_config,
                secret_projects=[],
            )
            workspace_path = Path(temp_dir) / 'PROJ-1'
            workspace_path.mkdir(parents=True)

            self.assertTrue(workspace_service.workspace_exists('PROJ-1'))

    def test_create_workspace_rejects_secret_project_leak(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_service = WorkspaceService(
                workspace_base_path=temp_dir,
                repository_config=self.repository_config,
                secret_projects=['secret'],
            )

            def fake_run(command, capture_output, text):
                target_path = Path(command[-1])
                target_path.mkdir(parents=True, exist_ok=True)
                return Mock(returncode=0, stderr='')

            with patch(
                'kato.data_layers.service.workspace_service.subprocess.run',
                side_effect=fake_run,
            ), patch.object(
                workspace_service,
                '_validate_secret_projects_absent',
                side_effect=RuntimeError('secret project secret leaked into workspace'),
            ):
                with self.assertRaisesRegex(RuntimeError, 'secret project secret leaked'):
                    workspace_service.create_workspace('PROJ-1', ['client'])

            self.assertFalse((Path(temp_dir) / 'PROJ-1').exists())
