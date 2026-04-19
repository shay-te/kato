from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from kato.data_layers.service.openhands_instance_service import (
    NoPortsAvailable,
    OpenHandsInstanceService,
)


class OpenHandsInstanceServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = OpenHandsInstanceService(
            docker_image='ghcr.io/all-hands-ai/openhands:latest',
            port_range_start=8000,
            port_range_end=8002,
            memory_limit='4g',
            cpu_limit='2',
        )

    def test_allocate_port_skips_used_ports(self) -> None:
        with patch(
            'kato.data_layers.service.openhands_instance_service.subprocess.run',
            return_value=Mock(stdout='0.0.0.0:8000->3000/tcp\n0.0.0.0:8001->3000/tcp\n'),
        ):
            self.assertEqual(self.service._allocate_port(), 8002)

    def test_allocate_port_raises_when_range_is_exhausted(self) -> None:
        with patch(
            'kato.data_layers.service.openhands_instance_service.subprocess.run',
            return_value=Mock(stdout='0.0.0.0:8000->3000/tcp\n0.0.0.0:8001->3000/tcp\n0.0.0.0:8002->3000/tcp\n'),
        ):
            with self.assertRaises(NoPortsAvailable):
                self.service._allocate_port()

    def test_create_container_builds_expected_docker_command(self) -> None:
        with patch(
            'kato.data_layers.service.openhands_instance_service.subprocess.run',
            side_effect=[
                Mock(stdout='0.0.0.0:8001->3000/tcp\n', returncode=0),
                Mock(stdout='container-id', returncode=0, stderr=''),
            ],
        ) as mock_run:
            session = self.service.create_container(
                'PROJ-1',
                '/var/kato/tasks/PROJ-1',
                env_vars={'OPENHANDS_LLM_MODEL': 'openai/gpt-4o'},
            )

        self.assertEqual(session.container_name, 'openhands-PROJ-1')
        self.assertEqual(session.port, 8000)
        self.assertEqual(session.url, 'http://localhost:8000')
        self.assertEqual(session.workspace_path, '/var/kato/tasks/PROJ-1')
        docker_run_command = mock_run.call_args_list[1].args[0]
        self.assertEqual(docker_run_command[:6], ['docker', 'run', '-d', '--name', 'openhands-PROJ-1', '-p'])
        self.assertIn('8000:3000', docker_run_command)
        self.assertIn('-v', docker_run_command)
        self.assertIn('/var/kato/tasks/PROJ-1:/workspace', docker_run_command)
        self.assertIn('--memory', docker_run_command)
        self.assertIn('4g', docker_run_command)
        self.assertIn('--cpus', docker_run_command)
        self.assertIn('2', docker_run_command)
        self.assertIn('-e', docker_run_command)
        self.assertIn('OPENHANDS_LLM_MODEL=openai/gpt-4o', docker_run_command)
        self.assertEqual(docker_run_command[-1], 'ghcr.io/all-hands-ai/openhands:latest')

    def test_create_container_raises_when_docker_run_fails(self) -> None:
        with patch(
            'kato.data_layers.service.openhands_instance_service.subprocess.run',
            side_effect=[
                Mock(stdout='', returncode=0),
                Mock(stdout='', returncode=1, stderr='permission denied'),
            ],
        ):
            with self.assertRaisesRegex(RuntimeError, 'permission denied'):
                self.service.create_container('PROJ-1', Path('/tmp/workspace'))

    def test_stop_and_remove_container_delegate_to_docker(self) -> None:
        with patch('kato.data_layers.service.openhands_instance_service.subprocess.run') as mock_run:
            self.service.stop_container('PROJ-1')
            self.service.remove_container('PROJ-2')

        self.assertEqual(mock_run.call_args_list[0].args[0], ['docker', 'stop', 'openhands-PROJ-1'])
        self.assertEqual(mock_run.call_args_list[1].args[0], ['docker', 'stop', 'openhands-PROJ-2'])
        self.assertEqual(mock_run.call_args_list[2].args[0], ['docker', 'rm', 'openhands-PROJ-2'])

    def test_rejects_invalid_port_range(self) -> None:
        with self.assertRaisesRegex(ValueError, 'port_range_start must be less than or equal to port_range_end'):
            OpenHandsInstanceService(
                docker_image='ghcr.io/all-hands-ai/openhands:latest',
                port_range_start=9000,
                port_range_end=8999,
            )
