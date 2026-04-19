from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from core_lib.data_layers.service.service import Service

from kato.helpers.logging_utils import configure_logger
from kato.helpers.text_utils import normalized_text, text_from_attr, text_from_mapping


class WorkspaceService(Service):
    """Create isolated task workspaces and clone only the required repositories."""

    def __init__(
        self,
        workspace_base_path: str,
        repository_config: Mapping[str, object] | Iterable[object],
        secret_projects: Iterable[str] | str | None = None,
        max_parallel_clones: int = 5,
        logger=None,
    ) -> None:
        self.logger = logger or configure_logger(self.__class__.__name__)
        self._workspace_base_path = Path(normalized_text(workspace_base_path))
        self._workspace_base_path.mkdir(parents=True, exist_ok=True)
        self._repository_config = self._normalize_repository_config(repository_config)
        self._secret_projects = self._normalize_secret_projects(secret_projects)
        self._max_parallel_clones = max(1, int(max_parallel_clones))

    def create_workspace(self, task_id: str, project_names: list[str]) -> Path:
        workspace_path = self._workspace_path(task_id)
        workspace_path.mkdir(parents=True, exist_ok=True)
        try:
            clone_targets = self._selected_clone_targets(project_names)
            self._clone_projects_parallel(workspace_path, clone_targets)
            self._validate_secret_projects_absent(workspace_path)
        except Exception:
            shutil.rmtree(workspace_path, ignore_errors=True)
            raise
        return workspace_path

    def cleanup_workspace(self, task_id: str) -> bool:
        workspace_path = self._workspace_path(task_id)
        if not workspace_path.exists():
            return False
        shutil.rmtree(workspace_path)
        return True

    def workspace_exists(self, task_id: str) -> bool:
        return self._workspace_path(task_id).exists()

    def _workspace_path(self, task_id: str) -> Path:
        return self._workspace_base_path / normalized_text(task_id)

    def _selected_clone_targets(self, project_names: list[str]) -> list[str]:
        selected_projects: list[str] = []
        for project_name in project_names:
            normalized_project_name = normalized_text(project_name)
            if not normalized_project_name:
                continue
            if normalized_project_name in self._secret_projects:
                continue
            if normalized_project_name not in self._repository_config:
                raise ValueError(
                    f"project '{normalized_project_name}' not found in repository config"
                )
            selected_projects.append(normalized_project_name)
        return selected_projects

    def _clone_projects_parallel(self, workspace_path: Path, project_names: list[str]) -> None:
        if not project_names:
            return

        failures: list[tuple[str, str]] = []
        with ThreadPoolExecutor(max_workers=self._max_parallel_clones) as executor:
            futures = {
                executor.submit(self._clone_project, workspace_path, project_name): project_name
                for project_name in project_names
            }
            for future in as_completed(futures):
                project_name = futures[future]
                try:
                    future.result()
                except Exception as exc:
                    failures.append((project_name, str(exc)))

        if failures:
            error_lines = '\n'.join(f'- {project_name}: {error}' for project_name, error in failures)
            raise RuntimeError(f'failed to clone required repositories:\n{error_lines}')

    def _clone_project(self, workspace_path: Path, project_name: str) -> None:
        repository = self._repository_config[project_name]
        git_url = self._repository_value(repository, 'git_url', 'remote_url')
        if not git_url:
            raise ValueError(f'missing git_url for repository {project_name}')

        branch_name = self._repository_value(
            repository,
            'branch',
            'destination_branch',
            default='main',
        )
        target_path = workspace_path / project_name
        command = [
            'git',
            'clone',
            '--depth',
            '1',
            '--single-branch',
            '--branch',
            branch_name,
            git_url,
            str(target_path),
        ]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            stderr = normalized_text(result.stderr) or 'git clone failed'
            raise RuntimeError(stderr)

    def _validate_secret_projects_absent(self, workspace_path: Path) -> None:
        for secret_project in sorted(self._secret_projects):
            if (workspace_path / secret_project).exists():
                raise RuntimeError(
                    f'secret project {secret_project} leaked into workspace'
                )

    @staticmethod
    def _normalize_secret_projects(
        secret_projects: Iterable[str] | str | None,
    ) -> set[str]:
        if secret_projects is None:
            return set()
        if isinstance(secret_projects, str):
            candidates = secret_projects.split(',')
        else:
            candidates = list(secret_projects)
        return {normalized_text(project) for project in candidates if normalized_text(project)}

    @staticmethod
    def _normalize_repository_config(
        repository_config: Mapping[str, object] | Iterable[object],
    ) -> dict[str, object]:
        if isinstance(repository_config, Mapping):
            return {normalized_text(key): value for key, value in repository_config.items()}

        normalized_config: dict[str, object] = {}
        for repository in repository_config or []:
            repository_name = WorkspaceService._repository_value(
                repository,
                'name',
                'id',
            )
            if not repository_name:
                raise ValueError('repository config entry is missing a name or id')
            normalized_config[repository_name] = repository
        return normalized_config

    @staticmethod
    def _repository_value(
        repository: object,
        *keys: str,
        default: str = '',
    ) -> str:
        for key in keys:
            value = normalized_text(text_from_mapping(repository, key))
            if value:
                return value
            value = normalized_text(text_from_attr(repository, key))
            if value:
                return value
        return normalized_text(default)
