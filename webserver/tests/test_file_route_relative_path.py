"""Regression: ``GET /api/sessions/<task>/file`` resolves repo-relative paths.

The Files tab renders repo-relative paths in the tree (e.g.
``dev_scripts/foo.py``) and the click handler forwards them verbatim
to ``/api/sessions/<task>/file?path=...``. Before the fix, the route
ran ``Path(path).resolve()`` which resolved against the kato process
cwd, not the workspace root — so every repo-relative click landed
outside the workspace and got a 403 ``path is outside the task
workspace``.

These tests pin:
  1. Repo-relative path → resolves into the matching workspace root.
  2. Absolute path inside workspace → still works (back-compat).
  3. Path-traversal attempt with ``..`` is still refused with 403.
  4. Path that doesn't exist returns 404, not 403.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kato_webserver.app import create_app


class _FakeRecord:
    def __init__(self, task_id: str) -> None:
        self.task_id = task_id


class _FakeManager:
    def __init__(self, records=None) -> None:
        self._records = records or []

    def list_records(self):
        return list(self._records)

    def get_record(self, task_id: str):
        for record in self._records:
            if record.task_id == task_id:
                return record
        return None

    def get_session(self, task_id):  # noqa: ARG002
        return None


class _FakeWorkspaceRecord:
    def __init__(self, task_id: str, repository_ids: list[str]) -> None:
        self.task_id = task_id
        self.repository_ids = list(repository_ids)


class _FakeWorkspaceManager:
    def __init__(self, *, records, repo_paths, workspace_path_for):
        self._records = records
        self._repo_paths = repo_paths
        self._workspace_path_for = workspace_path_for

    def get(self, task_id):
        for record in self._records:
            if record.task_id == task_id:
                return record
        return None

    def repository_path(self, task_id, repo_id):
        return Path(self._repo_paths.get((task_id, repo_id), '/missing'))

    def workspace_path(self, task_id):
        return Path(self._workspace_path_for.get(task_id, '/missing'))


class FileRouteRelativePathTests(unittest.TestCase):

    def setUp(self) -> None:
        # Real on-disk clone so the route's path-resolution + read
        # path exercises filesystem behaviour, not mock returns.
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace_root = Path(self._tmp.name) / 'TASK-1'
        self.clone_root = self.workspace_root / 'client'
        (self.clone_root / 'dev_scripts').mkdir(parents=True)
        # The file the user clicked in the screenshot.
        self.file_path = self.clone_root / 'dev_scripts' / 'export_users.py'
        self.file_path.write_text('print("hi")\n', encoding='utf-8')

        workspace_manager = _FakeWorkspaceManager(
            records=[_FakeWorkspaceRecord('TASK-1', ['client'])],
            repo_paths={('TASK-1', 'client'): str(self.clone_root)},
            workspace_path_for={'TASK-1': str(self.workspace_root)},
        )
        self.app = create_app(
            session_manager=_FakeManager(records=[_FakeRecord('TASK-1')]),
            workspace_manager=workspace_manager,
        )

    def test_repo_relative_path_resolves_into_workspace(self) -> None:
        # This is the bug from the screenshot: the tree gives the UI a
        # path like ``dev_scripts/export_users.py`` and the route must
        # join it with the workspace root, not the kato process cwd.
        response = self.app.test_client().get(
            '/api/sessions/TASK-1/file?path=dev_scripts/export_users.py',
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertIn('print("hi")', body.get('content', ''))

    def test_absolute_path_inside_workspace_still_works(self) -> None:
        # Back-compat: any caller that sends a fully-resolved
        # absolute path keeps working as before.
        response = self.app.test_client().get(
            f'/api/sessions/TASK-1/file?path={self.file_path}',
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn('print("hi")', response.get_json().get('content', ''))

    def test_traversal_with_relative_path_is_refused(self) -> None:
        # Even repo-relative resolution must not let ``..`` escape
        # the workspace root.
        response = self.app.test_client().get(
            '/api/sessions/TASK-1/file?path=../../etc/passwd',
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn('outside', response.get_json().get('error', ''))

    def test_relative_path_to_nonexistent_file_returns_404(self) -> None:
        # Resolves to a path under the workspace root but the file
        # doesn't exist → 404, not 403 (so the operator can tell
        # "wrong path" from "permission denied").
        response = self.app.test_client().get(
            '/api/sessions/TASK-1/file?path=dev_scripts/ghost.py',
        )
        self.assertEqual(response.status_code, 404)


if __name__ == '__main__':
    unittest.main()
