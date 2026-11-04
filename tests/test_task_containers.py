"""A task's Docker containers, found by its folder: listed, stopped when the
task is done, removed when it is deleted — and kato's sandbox left alone."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from container_core_lib.container_core_lib import FolderContainers
from container_core_lib.container_core_lib.tests.fakes import FakeDocker, compose_labels, raw_container
from sandbox_core_lib.sandbox_core_lib.manager import SANDBOX_CONTAINER_LABEL

from kato_core_lib.data_layers.service.task_containers import TaskContainers


class TaskContainersTests(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        (self.root / 'UNA-1' / 'api').mkdir(parents=True)
        task = str((self.root / 'UNA-1').resolve())
        self.workspaces = SimpleNamespace(workspace_path=lambda task_id: self.root / task_id)
        self.docker = FakeDocker()
        self.docker.add(raw_container('a', 'una-1-db-1', labels=compose_labels('una-1', f'{task}/api')))
        self.docker.add(raw_container('b', 'vite', binds=(f'{task}/api',), state='exited'))
        self.docker.add(raw_container('c', 'sandbox', binds=(task,), labels={SANDBOX_CONTAINER_LABEL: 'true'}))
        self.docker.add(raw_container('d', 'other-task', binds=(str(self.root / 'UNA-2'),)))
        self.later = []
        self.logger = Mock()
        self.containers = TaskContainers(
            workspace_manager=self.workspaces, logger=self.logger,
            containers=FolderContainers(self.docker.cli(), ignore_label=SANDBOX_CONTAINER_LABEL),
            run_later=self.later.append,
        )

    def test_a_tasks_containers_without_the_sandbox_or_anyone_elses(self) -> None:
        listed = self.containers.of_task('UNA-1')
        self.assertEqual([(c['name'], c['running']) for c in listed['containers']],
                         [('una-1-db-1', True), ('vite', False)])
        self.assertTrue(listed['available'])
        self.assertEqual(self.containers.of_task('UNA-404'), {'available': True, 'containers': [], 'reason': ''})

    def test_docker_that_cannot_be_asked_is_said_not_raised(self) -> None:
        self.docker.down = 'Cannot connect to the Docker daemon'
        self.assertEqual(self.containers.of_task('UNA-1'),
                         {'available': False, 'containers': [], 'reason': 'Cannot connect to the Docker daemon'})

    def test_stop_and_remove_act_on_the_task_only(self) -> None:
        self.assertEqual(self.containers.stop('UNA-1').containers, ('una-1-db-1',))
        self.assertEqual(self.containers.remove('UNA-1').containers, ('una-1-db-1', 'vite'))
        self.assertEqual(sorted(raw['Name'] for raw in self.docker.containers.values()), ['/other-task', '/sandbox'])
        self.assertEqual(self.containers.stop('UNA-404').containers, ())
        self.assertEqual(self.containers.remove('UNA-404').containers, ())

    def test_a_task_reaching_done_has_its_containers_stopped_off_the_done_path(self) -> None:
        self.containers.task_done('UNA-1')
        self.assertEqual(self.docker.containers['aaaa']['State']['Status'], 'running')  # not yet
        self.later.pop()()
        self.assertEqual(self.docker.containers['aaaa']['State']['Status'], 'exited')
        self.assertIn('stopped its containers', self.logger.info.call_args.args[0])

    def test_done_with_nothing_running_or_no_docker_says_nothing(self) -> None:
        self.containers.stop('UNA-1')
        self.containers.task_done('UNA-1')
        self.later.pop()()
        self.docker.down = 'not running'
        self.containers.task_done('UNA-1')
        self.later.pop()()
        self.logger.info.assert_not_called()
        self.logger.warning.assert_not_called()

    def test_a_container_that_will_not_stop_is_warned_about(self) -> None:
        self.docker.refuse['aaaa'] = 'permission denied'
        self.containers.task_done('UNA-1')
        self.later.pop()()
        self.assertIn('permission denied', self.logger.warning.call_args.args[-1])

    def test_no_workspaces_or_one_that_fails_has_no_folder(self) -> None:
        broken = TaskContainers(workspace_manager=SimpleNamespace(workspace_path=Mock(side_effect=OSError('x'))),
                                containers=Mock())
        self.assertEqual(broken.of_task('UNA-1')['containers'], [])
        self.assertEqual(TaskContainers(workspace_manager=None, containers=Mock()).stop('UNA-1').containers, ())

    def test_the_done_hook_runs_on_its_own_thread_by_default(self) -> None:
        done = []
        containers = TaskContainers(workspace_manager=self.workspaces, containers=Mock(), logger=self.logger)
        containers._stop_after_done = lambda task_id: done.append(task_id)
        containers.task_done('UNA-9')
        for _ in range(100):
            if done:
                break
            import time
            time.sleep(0.01)
        self.assertEqual(done, ['UNA-9'])

    def test_the_default_finds_containers_with_docker_and_skips_the_sandbox(self) -> None:
        containers = TaskContainers(workspace_manager=self.workspaces)
        self.assertEqual(containers._containers._ignore_label, SANDBOX_CONTAINER_LABEL)


if __name__ == '__main__':
    unittest.main()
