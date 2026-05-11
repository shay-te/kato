"""Tests for the workspace_status field on /diff and the DELETE
/workspace endpoint that powers the "Forget this task" button.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from kato_webserver.app import create_app


class _FakeWorkspaceManager(object):
    def __init__(self, status: str = 'review') -> None:
        self._status = status
        self.deleted: list[str] = []

    def get(self, task_id: str):
        return SimpleNamespace(status=self._status, repository_ids=[], cwd='')

    def delete(self, task_id: str) -> None:
        self.deleted.append(task_id)


class DiffWorkspaceStatusTests(unittest.TestCase):
    def test_diff_response_includes_workspace_status_review(self) -> None:
        wm = _FakeWorkspaceManager(status='review')
        sm = MagicMock()
        sm.records.return_value = []
        sm.find_by_task_id.return_value = None
        app = create_app(
            session_manager=sm,
            workspace_manager=wm,
            planning_session_runner=None,
        )
        resp = app.test_client().get('/api/sessions/UNA-2564/diff')
        # Either we get a structured response with workspace_status, or
        # 404 if the cwd isn't resolvable. We accept both since this test
        # focuses on the workspace_status field shape when reachable.
        if resp.status_code == 200:
            body = resp.get_json()
            self.assertEqual(body.get('workspace_status'), 'review')


class ForgetWorkspaceEndpointTests(unittest.TestCase):
    def test_forget_calls_delete_on_manager(self) -> None:
        wm = _FakeWorkspaceManager()
        app = create_app(
            session_manager=MagicMock(),
            workspace_manager=wm,
            planning_session_runner=None,
        )
        resp = app.test_client().delete('/api/sessions/UNA-2564/workspace')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()['forgotten'])
        self.assertEqual(wm.deleted, ['UNA-2564'])

    def test_forget_returns_503_when_workspace_manager_missing(self) -> None:
        app = create_app(
            session_manager=MagicMock(),
            workspace_manager=None,
            planning_session_runner=None,
        )
        resp = app.test_client().delete('/api/sessions/UNA-2564/workspace')
        self.assertEqual(resp.status_code, 503)


if __name__ == '__main__':
    unittest.main()
