"""The task header's containers: list, stop, remove — and removed with the task."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from container_core_lib.container_core_lib import FolderContainers, Outcome
from container_core_lib.container_core_lib.tests.fakes import FakeDocker, raw_container
from kato_core_lib.data_layers.service.task_containers import TaskContainers
from workspace_core_lib.workspace_core_lib import WorkspaceCoreLib

from kato_webserver.app import create_app
from kato_webserver.task_container_routes import task_containers_of


class TaskContainerRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        self.workspaces = WorkspaceCoreLib(
            root=str(root / 'workspaces'), max_parallel_tasks=1,
            metadata_filename='.kato-meta.json', preflight_log_filename='.kato-preflight.log',
        ).workspaces
        self.workspaces.create(task_id='UNA-1', repository_ids=['api'])
        task = str(self.workspaces.workspace_path('UNA-1').resolve())
        self.docker = FakeDocker()
        self.docker.add(raw_container('a', 'una-1-db-1', binds=(f'{task}/api',)))
        self.docker.add(raw_container('b', 'elsewhere', binds=('/somewhere/else',)))
        self.app = create_app(session_manager=None, workspace_manager=self.workspaces)
        self.app.config['TASK_CONTAINERS'] = TaskContainers(
            workspace_manager=self.workspaces, containers=FolderContainers(self.docker.cli()),
        )
        self.client = self.app.test_client()

    def test_list_stop_and_remove(self) -> None:
        listed = self.client.get('/api/sessions/UNA-1/containers').get_json()
        self.assertEqual([c['name'] for c in listed['containers']], ['una-1-db-1'])
        stopped = self.client.post('/api/sessions/UNA-1/containers/stop')
        self.assertEqual((stopped.status_code, stopped.get_json()['containers']), (200, ['una-1-db-1']))
        removed = self.client.post('/api/sessions/UNA-1/containers/remove').get_json()
        self.assertEqual(removed['containers'], ['una-1-db-1'])
        self.assertEqual(list(self.docker.containers), ['bbbb'])

    def test_a_refusal_answers_502_with_dockers_words(self) -> None:
        self.docker.refuse['aaaa'] = 'permission denied'
        answer = self.client.post('/api/sessions/UNA-1/containers/stop')
        self.assertEqual(answer.status_code, 502)
        self.assertEqual(answer.get_json()['problems'], ['una-1-db-1: permission denied'])
        self.assertEqual(self.client.post('/api/sessions/UNA-1/containers/start').status_code, 404)

    def test_deleting_the_task_removes_its_containers_and_says_so(self) -> None:
        answer = self.client.delete('/api/sessions/UNA-1/workspace')
        self.assertEqual(answer.status_code, 200, answer.get_json())
        self.assertEqual(answer.get_json()['containers']['containers'], ['una-1-db-1'])
        self.assertEqual(list(self.docker.containers), ['bbbb'])

    def test_a_delete_with_nothing_to_remove_or_a_docker_that_fails_still_deletes(self) -> None:
        self.docker.containers.pop('aaaa')
        quiet = self.client.delete('/api/sessions/UNA-1/workspace').get_json()
        self.assertEqual(quiet['containers'], {'containers': [], 'networks': [], 'volumes': [], 'problems': [], 'ok': True})
        self.workspaces.create(task_id='UNA-2', repository_ids=['api'])
        self.app.config['TASK_CONTAINERS'] = SimpleNamespace(of_task=Mock(side_effect=RuntimeError('broken')))
        answer = self.client.delete('/api/sessions/UNA-2/workspace')
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer.get_json()['containers']['problems'], ['broken'])

    def test_the_app_builds_its_one_task_containers_from_its_workspaces(self) -> None:
        app = create_app(session_manager=None, workspace_manager=self.workspaces)
        containers = task_containers_of(app)
        self.assertIs(task_containers_of(app), containers)
        self.assertIs(containers._get_workspace_manager(), self.workspaces)


if __name__ == '__main__':
    unittest.main()
