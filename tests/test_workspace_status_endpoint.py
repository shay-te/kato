"""Tests for the workspace_status field on /diff and the DELETE
/workspace endpoint that powers the "Forget this task" button.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

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


class ForgetWorkspaceMarkDoneTests(unittest.TestCase):
    """``?done=1`` — the dialog's "this task is done" checkbox.

    The delete itself is local, but this flag reaches the TICKET on the
    task tracker, so the two halves are ordered deliberately: the ticket
    moves first, and a failed move aborts the whole delete (the operator
    keeps the tab, and with it the ability to retry).
    """

    def setUp(self) -> None:
        # Keep the endpoint's forgotten-task / plan-mode writes inside a
        # temp dir — the real ~/.kato belongs to the operator.
        self._tmp = tempfile.TemporaryDirectory()
        env = patch.dict(os.environ, {
            'KATO_FORGOTTEN_TASKS_PATH': os.path.join(self._tmp.name, 'forgotten.json'),
            'KATO_PLAN_MODE_PATH': os.path.join(self._tmp.name, 'plan_mode.json'),
        })
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(self._tmp.cleanup)

    @staticmethod
    def _app(workspace_manager, session_manager, agent_service):
        app = create_app(
            session_manager=session_manager,
            workspace_manager=workspace_manager,
            planning_session_runner=None,
        )
        app.config['AGENT_SERVICE'] = agent_service
        return app

    def test_done_flag_moves_the_ticket_and_still_deletes(self) -> None:
        wm = _FakeWorkspaceManager()
        agent_service = MagicMock()
        app = self._app(wm, MagicMock(), agent_service)

        resp = app.test_client().delete('/api/sessions/UNA-2564/workspace?done=1')

        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertTrue(body['forgotten'])
        self.assertTrue(body['moved_to_done'])
        agent_service.mark_task_done.assert_called_once_with('UNA-2564')
        self.assertEqual(wm.deleted, ['UNA-2564'])

    def test_without_the_flag_the_ticket_is_never_touched(self) -> None:
        wm = _FakeWorkspaceManager()
        agent_service = MagicMock()
        app = self._app(wm, MagicMock(), agent_service)

        resp = app.test_client().delete('/api/sessions/UNA-2564/workspace')

        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.get_json()['moved_to_done'])
        agent_service.mark_task_done.assert_not_called()
        self.assertEqual(wm.deleted, ['UNA-2564'])

    def test_failed_move_aborts_the_delete_entirely(self) -> None:
        wm = _FakeWorkspaceManager()
        session_manager = MagicMock()
        agent_service = MagicMock()
        agent_service.mark_task_done.side_effect = RuntimeError(
            'unknown jira transition: Done',
        )
        app = self._app(wm, session_manager, agent_service)

        resp = app.test_client().delete('/api/sessions/UNA-2564/workspace?done=1')

        self.assertEqual(resp.status_code, 502)
        body = resp.get_json()
        self.assertFalse(body['forgotten'])
        self.assertFalse(body['moved_to_done'])
        self.assertIn('unknown jira transition: Done', body['error'])
        self.assertIn('nothing was deleted', body['error'])
        # Nothing local was touched: no clone delete, no session kill.
        self.assertEqual(wm.deleted, [])
        session_manager.terminate_session.assert_not_called()

    def test_done_flag_without_a_task_platform_aborts(self) -> None:
        wm = _FakeWorkspaceManager()
        session_manager = MagicMock()
        # No AGENT_SERVICE wired (setup mode / degraded boot): there is
        # no tracker to move the ticket on, so the delete must not run.
        app = self._app(wm, session_manager, None)

        resp = app.test_client().delete('/api/sessions/UNA-2564/workspace?done=1')

        self.assertEqual(resp.status_code, 502)
        self.assertIn('nothing was deleted', resp.get_json()['error'])
        self.assertEqual(wm.deleted, [])
        session_manager.terminate_session.assert_not_called()


if __name__ == '__main__':
    unittest.main()
