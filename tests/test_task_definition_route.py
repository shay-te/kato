"""``GET /api/sessions/<task>/task-definition``: read a task's description again.

Report: "When a manual task is added, is there any option to view the original
task description again? Clicking the task tab does not show it, and the chat
does not show the original prompt." The route reads the real workspace record —
the one the agent's Task definition is built from.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from kato_webserver.app import create_app
from tests.chaos_lib import build_real_workspace_service


class _FakeManager:
    def list_records(self):
        return []

    def get_record(self, task_id):  # noqa: ARG002
        return None

    def get_session(self, task_id):  # noqa: ARG002
        return None


class TaskDefinitionRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix='task-definition-')
        self.addCleanup(tmp.cleanup)
        self.workspaces = build_real_workspace_service(Path(tmp.name))
        self.client = create_app(
            session_manager=_FakeManager(), workspace_manager=self.workspaces,
        ).test_client()

    def test_the_task_s_title_and_description_as_it_was_created(self) -> None:
        self.workspaces.create(
            task_id='LOCAL-3', task_summary='Retry the export job',
            task_description='The export job fails on timeouts.\n\n- retry 3 times\n- back off',
            repository_ids=['api'],
        )
        response = self.client.get('/api/sessions/LOCAL-3/task-definition')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {
            'task_id': 'LOCAL-3',
            'summary': 'Retry the export job',
            'description': 'The export job fails on timeouts.\n\n- retry 3 times\n- back off',
        })

    def test_a_task_with_no_description_answers_an_empty_one(self) -> None:
        self.workspaces.create(task_id='UNA-9', task_summary='Title only', repository_ids=['api'])
        body = self.client.get('/api/sessions/UNA-9/task-definition').get_json()
        self.assertEqual((body['summary'], body['description']), ('Title only', ''))

    def test_an_unknown_task_is_404(self) -> None:
        response = self.client.get('/api/sessions/NOPE-1/task-definition')
        self.assertEqual(response.status_code, 404)
        self.assertIn('NOPE-1', response.get_json()['error'])

    def test_the_tab_list_still_leaves_the_description_out(self) -> None:
        # Read on demand only: the 5s poll stays slim.
        self.workspaces.create(task_id='LOCAL-3', task_summary='T', task_description='long text',
                               repository_ids=['api'])
        tabs = self.client.get('/api/sessions').get_json()
        self.assertTrue(all('task_description' not in tab for tab in tabs))


if __name__ == '__main__':
    unittest.main()
