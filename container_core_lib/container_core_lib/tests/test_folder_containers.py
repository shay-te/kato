"""Which containers belong to a folder, and stopping or removing them."""

import os
import tempfile
import unittest
from pathlib import Path

from container_core_lib.container_core_lib.data import Container, Outcome
from container_core_lib.container_core_lib.docker_cli import DockerCli
from container_core_lib.container_core_lib.folder_containers import FolderContainers
from container_core_lib.container_core_lib.tests.fakes import FakeDocker, compose_labels, raw_container

TASK = '/work/UNA-1'


class BelongsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.docker = FakeDocker()
        self.folder = FolderContainers(self.docker.cli(), ignore_label='sandbox.owner')

    def names(self, folder: str = TASK) -> list[str]:
        return [container.name for container in self.folder.in_folder(folder)]

    def test_a_compose_project_in_the_folder_and_a_mount_from_it(self) -> None:
        self.docker.add(raw_container('a', 'una-1-db-1', labels=compose_labels('una-1', f'{TASK}/api')))
        self.docker.add(raw_container('b', 'dev-server', binds=(f'{TASK}/web',)))
        self.docker.add(raw_container('c', 'by-file', labels={
            'com.docker.compose.project.config_files': f'/elsewhere/a.yaml,{TASK}/api/compose.yaml',
        }))
        self.docker.add(raw_container('d', 'someone-elses', labels=compose_labels('x', '/work/UNA-10')))
        self.docker.add(raw_container('e', 'named-volume-only', volumes=('data',)))
        self.docker.add(raw_container('f', 'the-sandbox', binds=(TASK,), labels={'sandbox.owner': '1'}))
        self.assertEqual(self.names(), ['by-file', 'dev-server', 'una-1-db-1'])

    def test_running_first_then_by_name_and_described(self) -> None:
        self.docker.add(raw_container('a', 'zz', binds=(TASK,)))
        self.docker.add(raw_container('b', 'aa', binds=(TASK,), state='exited', image='redis:7'))
        found = self.folder.in_folder(TASK)
        self.assertEqual([c.name for c in found], ['zz', 'aa'])
        self.assertEqual(found[1], Container(id='bbbb', name='aa', image='redis:7', state='exited'))
        self.assertEqual(found[0].to_dict()['running'], True)

    def test_odd_descriptions_never_break_the_list(self) -> None:
        self.docker.add({'Id': 'xxxx', 'Name': '/odd', 'Config': None, 'State': 'up', 'Mounts': ['nope']})
        self.docker.add({'Id': 'yyyy', 'Config': {'Labels': None}})
        self.assertEqual(self.names(), [])
        self.assertEqual(self.names(''), [])

    def test_the_folder_through_a_link(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            real = Path(root) / 'real'
            (real / 'UNA-1').mkdir(parents=True)
            link = Path(root) / 'link'
            os.symlink(real, link)
            self.docker.add(raw_container('a', 'via-real', binds=(str((real / 'UNA-1').resolve()),)))
            self.docker.add(raw_container('b', 'via-link', binds=(str(link / 'UNA-1'),)))
            self.assertEqual(self.names(str(link / 'UNA-1')), ['via-link', 'via-real'])

    def test_no_ignore_label_ignores_nothing(self) -> None:
        folder = FolderContainers(self.docker.cli())
        self.docker.add(raw_container('f', 'the-sandbox', binds=(TASK,), labels={'sandbox.owner': '1'}))
        self.assertEqual([c.name for c in folder.in_folder(TASK)], ['the-sandbox'])


class ActionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.docker = FakeDocker()
        self.folder = FolderContainers(self.docker.cli())
        self.docker.add(raw_container('a', 'una-1-db-1', labels=compose_labels('una-1', TASK)))
        self.docker.add(raw_container('b', 'una-1-web-1', labels=compose_labels('una-1', TASK), state='exited'))
        self.docker.add(raw_container('c', 'dev-server', binds=(TASK,)))
        self.docker.add(raw_container('d', 'not-mine', binds=('/work/UNA-2',)))
        self.docker.networks = {'una-1_default': 'una-1', 'una-2_default': 'una-2'}
        self.docker.volumes = {'una-1_pgdata': 'una-1'}

    def test_stop_stops_the_running_ones_only(self) -> None:
        outcome = self.folder.stop(TASK)
        self.assertEqual(outcome, Outcome(containers=('dev-server', 'una-1-db-1')))
        self.assertEqual(self.docker.containers['dddd']['State']['Status'], 'running')
        self.assertEqual([call[0] for call in self.docker.calls].count('stop'), 2)

    def test_a_refusal_is_reported_and_the_rest_still_stop(self) -> None:
        self.docker.refuse['cccc'] = 'permission denied'
        outcome = self.folder.stop(TASK)
        self.assertEqual(outcome.containers, ('una-1-db-1',))
        self.assertEqual(outcome.problems, ('dev-server: permission denied',))
        self.assertFalse(outcome.ok)

    def test_remove_takes_the_containers_and_what_compose_made_for_them(self) -> None:
        outcome = self.folder.remove(TASK)
        self.assertEqual(outcome.containers, ('dev-server', 'una-1-db-1', 'una-1-web-1'))  # running first
        self.assertEqual((outcome.networks, outcome.volumes), (('una-1_default',), ('una-1_pgdata',)))
        self.assertEqual(list(self.docker.containers), ['dddd'])
        self.assertEqual(self.docker.networks, {'una-2_default': 'una-2'})
        self.assertTrue(outcome.to_dict()['ok'])

    def test_a_project_with_a_container_left_keeps_its_network_and_volumes(self) -> None:
        self.docker.add(raw_container('e', 'una-1-worker-1', labels=compose_labels('una-1', '/elsewhere')))
        outcome = self.folder.remove(TASK)
        self.assertEqual((outcome.networks, outcome.volumes), ((), ()))
        self.assertIn('una-1_default', self.docker.networks)

    def test_a_container_refused_keeps_its_project_and_says_why(self) -> None:
        self.docker.refuse['aaaa'] = 'device busy'
        self.docker.refuse['una-1_pgdata'] = 'volume in use'
        outcome = self.folder.remove(TASK)
        self.assertIn('una-1-db-1: device busy', outcome.problems)
        self.assertIn('una-1_default', self.docker.networks)  # the project still has a container

    def test_a_network_or_volume_refused_is_reported(self) -> None:
        self.docker.refuse['una-1_pgdata'] = 'volume in use'
        outcome = self.folder.remove(TASK)
        self.assertEqual(outcome.networks, ('una-1_default',))
        self.assertEqual(outcome.problems, ('una-1_pgdata: volume in use',))

    def test_docker_going_away_midway_is_reported(self) -> None:
        def stop_after_removing(argv, timeout):
            answer = self.docker(argv, timeout)
            if argv[1] == 'rm' and len(self.docker.containers) == 1:
                self.docker.down = 'Docker Desktop is stopping'
            return answer
        folder = FolderContainers(DockerCli('docker', run=stop_after_removing))
        outcome = folder.remove(TASK)
        self.assertEqual(len(outcome.containers), 3)
        self.assertEqual(outcome.problems, ('Docker Desktop is stopping',))

    def test_docker_that_cannot_be_asked_is_one_problem(self) -> None:
        self.docker.down = 'Cannot connect to the Docker daemon'
        self.assertEqual(self.folder.stop(TASK).problems, ('Cannot connect to the Docker daemon',))
        self.assertEqual(self.folder.remove(TASK).problems, ('Cannot connect to the Docker daemon',))

    def test_nothing_of_compose_removes_no_network(self) -> None:
        folder = FolderContainers(self.docker.cli())
        outcome = folder.remove('/work/UNA-2')
        self.assertEqual(outcome, Outcome(containers=('not-mine',)))


if __name__ == '__main__':
    unittest.main()
