"""A to Z: an agent works in a task's folder and starts containers there — a
Compose stack and a dev server. Another task has its own, and the host runs
its sandbox over the folder. Then the task ends: its containers are found,
stopped, and finally removed with what Compose made for them, and nobody
else's are touched."""

import unittest

from container_core_lib.container_core_lib import DockerUnavailable, FolderContainers, Outcome
from container_core_lib.container_core_lib.tests.fakes import FakeDocker, compose_labels, raw_container

TASK = 'C:\\Users\\dana\\.work\\UNA-1'
DESKTOP = '/run/desktop/mnt/host/c/Users/dana/.work/UNA-1'


class FlowTests(unittest.TestCase):
    def test_a_task_s_containers_from_start_to_cleanup(self) -> None:
        docker = FakeDocker()
        # The agent's Compose stack, as Docker Desktop on Windows reports it,
        docker.add(raw_container('a', 'una-1-db-1', labels=compose_labels('una-1', f'{TASK}\\api')))
        docker.add(raw_container('b', 'una-1-cache-1', image='redis:7', labels=compose_labels('una-1', f'{TASK}\\api')))
        # its dev server, mounting the clone,
        docker.add(raw_container('c', 'vite', image='node:22', binds=(f'{DESKTOP}/web',)))
        # another task's database, and the host's sandbox over this folder.
        docker.add(raw_container('d', 'una-2-db-1', labels=compose_labels('una-2', 'C:\\Users\\dana\\.work\\UNA-2')))
        docker.add(raw_container('e', 'sandbox-una-1', binds=(DESKTOP,), labels={'sandbox': 'true'}))
        docker.networks = {'una-1_default': 'una-1', 'una-2_default': 'una-2'}
        docker.volumes = {'una-1_pgdata': 'una-1', 'una-2_pgdata': 'una-2'}
        folder = FolderContainers(docker.cli(), ignore_label='sandbox')

        found = folder.in_folder('c:/users/dana/.work/una-1')
        self.assertEqual([c.name for c in found], ['una-1-cache-1', 'una-1-db-1', 'vite'])
        self.assertTrue(all(c.running for c in found))

        # The task is done: stopped, kept.
        self.assertEqual(folder.stop(TASK), Outcome(containers=('una-1-cache-1', 'una-1-db-1', 'vite')))
        self.assertEqual({c.state for c in folder.in_folder(TASK)}, {'exited'})
        self.assertEqual(folder.stop(TASK), Outcome())  # nothing left running

        # The task is deleted: removed, with its project's network and volume.
        outcome = folder.remove(TASK)
        self.assertEqual(outcome.containers, ('una-1-cache-1', 'una-1-db-1', 'vite'))
        self.assertEqual((outcome.networks, outcome.volumes), (('una-1_default',), ('una-1_pgdata',)))
        self.assertEqual(folder.in_folder(TASK), [])
        self.assertEqual(sorted(raw['Name'] for raw in docker.containers.values()), ['/sandbox-una-1', '/una-2-db-1'])
        self.assertEqual((docker.networks, docker.volumes), ({'una-2_default': 'una-2'}, {'una-2_pgdata': 'una-2'}))

        # Docker stopped meanwhile: said, never raised from an action.
        docker.down = 'Cannot connect to the Docker daemon'
        self.assertEqual(folder.remove(TASK).problems, ('Cannot connect to the Docker daemon',))
        with self.assertRaises(DockerUnavailable):
            folder.in_folder(TASK)


if __name__ == '__main__':
    unittest.main()
