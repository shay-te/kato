"""When preparing a local task fails, the agent is never started on it."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from flask import Flask

from kato_core_lib.data_layers.data_access.task_data_access import TaskDataAccess
from kato_core_lib.data_layers.service.local_task_routing import LocalAwareTaskService
from kato_core_lib.data_layers.service.local_task_service import (
    START_CHAT,
    START_IMPLEMENT,
    START_PLAN,
    LocalTaskService,
    opening_message,
)
from kato_webserver.local_task_routes import register_local_task_routes
from local_task_core_lib.local_task_core_lib.store import LocalTaskStore
from tests.chaos_lib import build_real_workspace_service


class _Repositories(object):
    """Resolves each ``kato:repo:<id>`` tag to a repository named ``<id>``."""

    def resolve_task_repositories(self, task):
        return [SimpleNamespace(id=tag.rsplit(':', 1)[-1]) for tag in task.tags]


class _Harness(unittest.TestCase):

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.store = LocalTaskStore(root / 'local_tasks.json')
        config = SimpleNamespace(project='P', assignee='me', issue_states=['Todo'])
        self.tasks = LocalAwareTaskService(config, TaskDataAccess(config, object()), self.store)
        self.workspace = build_real_workspace_service(root / 'workspaces')
        self.prepared: list[str] = []
        self.context = SimpleNamespace(expected_branch='LOCAL-1')
        self.service = LocalTaskService(
            task_service=self.tasks, repository_service=_Repositories(),
            workspace_manager=self.workspace, prepare_workspace=self._prepare,
            approval_check=lambda repositories: [],
        )

    def _prepare(self, task):
        self.prepared.append(task.id)
        if isinstance(self.context, Exception):
            raise self.context
        return self.context


class PrepareTests(_Harness):

    def test_a_ready_branch_is_reported_ready(self) -> None:
        task = self.service.create('Title', 'Body', ['api', 'API', ' '])
        self.assertEqual(task.tags, ['kato:repo:api'])
        self.assertTrue(self.service.prepare(task.id))
        self.assertEqual(self.prepared, [task.id])
        self.assertEqual(self.workspace.get(task.id).repository_ids, ['api'])

    def test_no_branch_means_not_ready(self) -> None:
        task = self.service.create('Title', 'Body', ['api'])
        self.context = SimpleNamespace(expected_branch='')
        self.assertFalse(self.service.prepare(task.id))

    def test_a_task_deleted_meanwhile_is_not_prepared(self) -> None:
        self.assertFalse(self.service.prepare('LOCAL-9'))
        self.assertEqual(self.prepared, [])


class OpeningMessageTests(unittest.TestCase):

    def test_plan_asks_for_a_plan_and_chat_says_nothing(self) -> None:
        self.assertIn('plan', opening_message(START_PLAN))
        self.assertIn('approve', opening_message(START_PLAN))
        self.assertEqual(opening_message(START_IMPLEMENT), 'Implement this task.')
        self.assertEqual(opening_message(START_CHAT), '')


class RouteFailureTests(_Harness):

    def client(self):
        app = Flask(__name__)
        app.config['AGENT_SERVICE'] = SimpleNamespace(local_tasks=self.service)
        self.chats: list[tuple[str, str]] = []
        self.settings: list[tuple] = []
        register_local_task_routes(
            app,
            set_mode=lambda task_id, mode: self.settings.append(('mode', task_id, mode)),
            set_override=lambda key, task_id, value: self.settings.append((key, task_id, value)),
            effort_levels=lambda: ['high'],
            start_chat=lambda task_id, text: self.chats.append((task_id, text)),
            run_in_background=lambda work, name: work(),
        )
        return app.test_client()

    def post(self, client, **body):
        payload = {'summary': 'T', 'repositories': ['api'], **body}
        return client.post('/api/local-tasks', json=payload)

    def test_settings_are_applied_before_the_chat_starts(self) -> None:
        client = self.client()
        response = self.post(client, model='opus', effort='HIGH')
        self.assertEqual(response.status_code, 202)
        self.assertEqual(self.settings, [
            ('mode', 'LOCAL-1', 'plan'),
            ('TASK_MODEL_OVERRIDES', 'LOCAL-1', 'opus'),
            ('TASK_EFFORT_OVERRIDES', 'LOCAL-1', 'high'),
        ])
        self.assertEqual([task_id for task_id, _ in self.chats], ['LOCAL-1'])

    def test_a_failed_preparation_starts_no_chat(self) -> None:
        client = self.client()
        self.context = SimpleNamespace(expected_branch='')
        self.assertEqual(self.post(client).status_code, 202)
        self.context = RuntimeError('clone exploded')
        self.assertEqual(self.post(client).status_code, 202)
        self.assertEqual(self.chats, [])

    def test_not_wired_is_503(self) -> None:
        app = Flask(__name__)
        register_local_task_routes(
            app, set_mode=None, set_override=None, effort_levels=None, start_chat=None,
        )
        response = app.test_client().post('/api/local-tasks', json={})
        self.assertEqual(response.status_code, 503)


if __name__ == '__main__':
    unittest.main()
