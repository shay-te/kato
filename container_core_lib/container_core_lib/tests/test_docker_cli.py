"""The Docker command line: bounded, no terminal, and "cannot be asked" told apart."""

import subprocess
import unittest
from unittest import mock

from container_core_lib.container_core_lib.docker_cli import Answer, DockerCli, run_docker
from container_core_lib.container_core_lib.errors import DockerUnavailable
from container_core_lib.container_core_lib.tests.fakes import FakeDocker, raw_container


class RunDockerTests(unittest.TestCase):
    def test_no_terminal_no_window_and_a_time_limit(self) -> None:
        done = subprocess.CompletedProcess(['docker'], 0, stdout='ok', stderr=None)
        with mock.patch('subprocess.run', return_value=done) as run:
            self.assertEqual(run_docker(['docker', 'ps'], 7.0), Answer(0, 'ok', ''))
        kwargs = run.call_args.kwargs
        self.assertIs(kwargs['stdin'], subprocess.DEVNULL)
        self.assertEqual(kwargs['timeout'], 7.0)
        self.assertFalse(kwargs['check'])

    def test_a_command_that_cannot_run_or_finish_is_none(self) -> None:
        for error in (OSError('no such file'), subprocess.TimeoutExpired('docker', 1)):
            with mock.patch('subprocess.run', side_effect=error):
                self.assertIsNone(run_docker(['docker', 'ps'], 1.0))


class DockerCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.docker = FakeDocker()
        self.cli = self.docker.cli()

    def test_every_container_as_inspect_describes_it(self) -> None:
        self.assertEqual(self.cli.containers(), [])
        self.docker.add(raw_container('a', 'db'))
        self.docker.add(raw_container('b', 'web'))
        self.assertEqual([raw['Name'] for raw in self.cli.containers()], ['/db', '/web'])
        self.assertEqual(self.docker.calls[-1][0], 'inspect')

    def test_one_removed_between_the_two_calls_leaves_the_rest(self) -> None:
        self.docker.add(raw_container('a', 'db'))
        self.docker.add(raw_container('b', 'web'))
        self.docker.vanish_before_inspect = {'aaaa'}
        self.assertEqual([raw['Name'] for raw in self.cli.containers()], ['/web'])

    def test_an_unreadable_or_odd_answer(self) -> None:
        cli = DockerCli('docker', run=lambda argv, timeout: Answer(0, 'x1\n' if argv[1] == 'ps' else 'not json'))
        with self.assertRaises(DockerUnavailable):
            cli.containers()
        odd = DockerCli('docker', run=lambda argv, timeout: Answer(0, 'x1\n' if argv[1] == 'ps' else '{"a": 1}'))
        self.assertEqual(odd.containers(), [])
        mixed = DockerCli('docker', run=lambda argv, timeout: Answer(0, 'x1\n' if argv[1] == 'ps' else '[1, {"Id": "x"}]'))
        self.assertEqual(mixed.containers(), [{'Id': 'x'}])
        empty = DockerCli('docker', run=lambda argv, timeout: Answer(0, 'x1\n' if argv[1] == 'ps' else ''))
        self.assertEqual(empty.containers(), [])

    def test_docker_that_cannot_be_asked(self) -> None:
        with self.assertRaisesRegex(DockerUnavailable, 'not installed'):
            DockerCli(which=lambda name: None).containers()
        with self.assertRaisesRegex(DockerUnavailable, 'did not answer'):
            DockerCli('docker', run=lambda argv, timeout: None).containers()
        self.docker.down = 'Cannot connect to the Docker daemon. Is the docker daemon running?'
        with self.assertRaisesRegex(DockerUnavailable, 'Is the docker daemon running'):
            self.cli.containers()
        silent = DockerCli('docker', run=lambda argv, timeout: Answer(1))
        with self.assertRaisesRegex(DockerUnavailable, 'docker ps failed'):
            silent.containers()

    def test_the_binary_on_the_path_is_found(self) -> None:
        seen = []
        cli = DockerCli(which=lambda name: f'/usr/bin/{name}', run=lambda argv, timeout: seen.append(argv) or Answer(0))
        cli.containers()
        self.assertEqual(seen[0][0], '/usr/bin/docker')

    def test_actions_answer_why_not(self) -> None:
        self.docker.add(raw_container('a', 'db'))
        self.assertEqual(self.cli.stop('aaaa'), '')
        self.docker.refuse['aaaa'] = 'permission denied'
        self.assertEqual(self.cli.remove('aaaa'), 'permission denied')
        self.assertEqual(self.docker.calls[-1], ['rm', '--force', '--volumes', 'aaaa'])
        quiet = DockerCli('docker', run=lambda argv, timeout: Answer(1))
        self.assertEqual(quiet.stop('x'), 'docker stop x failed')

    def test_a_projects_networks_and_volumes(self) -> None:
        self.docker.networks = {'n1': 'una-1', 'n2': 'other'}
        self.docker.volumes = {'una-1_data': 'una-1'}
        self.assertEqual(self.cli.project_networks('una-1'), ['n1'])
        self.assertEqual(self.cli.project_volumes('una-1'), ['una-1_data'])
        self.assertIn('label=com.docker.compose.project=una-1', self.docker.calls[-1])
        self.assertEqual(self.cli.remove_network('n1'), '')
        self.assertEqual(self.cli.remove_volume('una-1_data'), '')
        self.assertEqual((self.docker.networks, self.docker.volumes), ({'n2': 'other'}, {}))


if __name__ == '__main__':
    unittest.main()
