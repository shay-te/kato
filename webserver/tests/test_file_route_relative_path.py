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
import subprocess
import unittest
from pathlib import Path

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


class _FakeAgentService:
    def __init__(self, base_branch: str) -> None:
        self._base_branch = base_branch

    def configured_destination_branch(self, repo_id):  # noqa: ARG002
        return self._base_branch


def _run_git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ['git', '-C', str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
    )


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


class FileRouteMultiRepoTests(unittest.TestCase):
    """Regression: multi-repo task must look up the right clone.

    With two repos on the task, a click on a file that only exists in
    repo B used to land on repo A's resolved candidate first (because
    the joined path is *also* lexically inside repo A's root) and
    return ``file not found`` even though the file existed in B. The
    route now prefers a candidate whose file actually exists.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace_root = Path(self._tmp.name) / 'TASK-2'
        self.repo_a = self.workspace_root / 'ob-love-admin-client'
        self.repo_b = self.workspace_root / 'email-core-lib'
        # Repo A has docker/, but NOT email_core_lib/email_core_lib.py.
        (self.repo_a / 'docker').mkdir(parents=True)
        # Repo B has the file the operator clicked.
        (self.repo_b / 'email_core_lib').mkdir(parents=True)
        target = self.repo_b / 'email_core_lib' / 'email_core_lib.py'
        target.write_text('# email lib\n', encoding='utf-8')

        workspace_manager = _FakeWorkspaceManager(
            records=[_FakeWorkspaceRecord(
                'TASK-2', ['ob-love-admin-client', 'email-core-lib'],
            )],
            repo_paths={
                ('TASK-2', 'ob-love-admin-client'): str(self.repo_a),
                ('TASK-2', 'email-core-lib'): str(self.repo_b),
            },
            workspace_path_for={'TASK-2': str(self.workspace_root)},
        )
        self.app = create_app(
            session_manager=_FakeManager(records=[_FakeRecord('TASK-2')]),
            workspace_manager=workspace_manager,
        )

    def test_clicking_file_in_second_repo_finds_it(self) -> None:
        # Path is repo-relative inside repo B. Repo A is first in the
        # roots list — the route must keep searching past it.
        response = self.app.test_client().get(
            '/api/sessions/TASK-2/file?path=email_core_lib/email_core_lib.py',
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn('email lib', response.get_json().get('content', ''))

    def test_nonexistent_in_either_repo_is_404_not_403(self) -> None:
        # When the relative path lands inside SOME root but doesn't
        # exist in any of them, return 404 — 403 is reserved for
        # "path escaped every root".
        response = self.app.test_client().get(
            '/api/sessions/TASK-2/file?path=email_core_lib/ghost.py',
        )
        self.assertEqual(response.status_code, 404)


class BaseFileRouteTests(unittest.TestCase):
    """The diff context expander needs file content at the diff base."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace_root = Path(self._tmp.name) / 'TASK-3'
        self.repo = self.workspace_root / 'client'
        self.repo.mkdir(parents=True)
        _run_git(self.repo, 'init', '-b', 'main')
        _run_git(self.repo, 'config', 'user.email', 'kato@example.com')
        _run_git(self.repo, 'config', 'user.name', 'Kato Test')
        (self.repo / 'src').mkdir()
        target = self.repo / 'src' / 'promises.scss'
        target.write_text("@import 'base.scss';\n.base { color: red; }\n", encoding='utf-8')
        _run_git(self.repo, 'add', 'src/promises.scss')
        _run_git(self.repo, 'commit', '-m', 'base')
        _run_git(self.repo, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
        target.write_text("@import 'base.scss';\n.base { color: blue; }\n", encoding='utf-8')

        workspace_manager = _FakeWorkspaceManager(
            records=[_FakeWorkspaceRecord('TASK-3', ['client'])],
            repo_paths={('TASK-3', 'client'): str(self.repo)},
            workspace_path_for={'TASK-3': str(self.workspace_root)},
        )
        self.app = create_app(
            session_manager=_FakeManager(records=[_FakeRecord('TASK-3')]),
            workspace_manager=workspace_manager,
            agent_service=_FakeAgentService('main'),
        )

    def test_base_file_route_reads_origin_base_not_worktree(self) -> None:
        response = self.app.test_client().get(
            '/api/sessions/TASK-3/base-file'
            '?repo=client&path=src/promises.scss',
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertIn('color: red', body.get('content', ''))
        self.assertNotIn('color: blue', body.get('content', ''))
        self.assertEqual(body.get('base'), 'main')

    def test_base_file_route_refuses_paths_outside_repo(self) -> None:
        response = self.app.test_client().get(
            '/api/sessions/TASK-3/base-file'
            '?repo=client&path=../../etc/passwd',
        )
        self.assertEqual(response.status_code, 403)


if __name__ == '__main__':
    unittest.main()
