from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from core_lib.data_layers.service.service import Service

from kato.helpers.logging_utils import configure_logger
from kato.helpers.text_utils import normalized_text


class NoPortsAvailable(RuntimeError):
    """Raised when the configured port range is exhausted."""


@dataclass(frozen=True)
class OpenHandsSession:
    """Describe a task-specific OpenHands container."""

    task_id: str
    container_name: str
    port: int
    url: str
    workspace_path: str


class OpenHandsInstanceService(Service):
    """Create isolated OpenHands containers with task-specific ports."""

    def __init__(
        self,
        docker_image: str,
        port_range_start: int = 8000,
        port_range_end: int = 8999,
        memory_limit: str = '4g',
        cpu_limit: str = '2',
        logger=None,
    ) -> None:
        self.logger = logger or configure_logger(self.__class__.__name__)
        self._docker_image = normalized_text(docker_image)
        if not self._docker_image:
            raise ValueError('docker_image is required')
        self._port_range_start = int(port_range_start)
        self._port_range_end = int(port_range_end)
        if self._port_range_start > self._port_range_end:
            raise ValueError('port_range_start must be less than or equal to port_range_end')
        self._memory_limit = normalized_text(memory_limit)
        self._cpu_limit = normalized_text(cpu_limit)

    def create_container(
        self,
        task_id: str,
        workspace_path: str | Path,
        env_vars: dict[str, str] | None = None,
    ) -> OpenHandsSession:
        container_name = self._container_name(task_id)
        port = self._allocate_port()
        command = self._docker_run_command(
            container_name,
            port,
            workspace_path,
            env_vars or {},
        )
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            stderr = normalized_text(result.stderr) or 'failed to start OpenHands container'
            raise RuntimeError(stderr)
        return OpenHandsSession(
            task_id=normalized_text(task_id),
            container_name=container_name,
            port=port,
            url=f'http://localhost:{port}',
            workspace_path=str(Path(workspace_path)),
        )

    def stop_container(self, task_id: str) -> None:
        subprocess.run(['docker', 'stop', self._container_name(task_id)], check=False)

    def remove_container(self, task_id: str) -> None:
        container_name = self._container_name(task_id)
        subprocess.run(['docker', 'stop', container_name], check=False)
        subprocess.run(['docker', 'rm', container_name], check=False)

    def _docker_run_command(
        self,
        container_name: str,
        port: int,
        workspace_path: str | Path,
        env_vars: dict[str, str],
    ) -> list[str]:
        command = [
            'docker',
            'run',
            '-d',
            '--name',
            container_name,
            '-p',
            f'{port}:3000',
            '-v',
            f'{Path(workspace_path)}:/workspace',
        ]
        if self._memory_limit:
            command.extend(['--memory', self._memory_limit])
        if self._cpu_limit:
            command.extend(['--cpus', self._cpu_limit])
        for key in sorted(env_vars):
            command.extend(['-e', f'{key}={env_vars[key]}'])
        command.append(self._docker_image)
        return command

    def _allocate_port(self) -> int:
        result = subprocess.run(
            ['docker', 'ps', '--format', '{{.Ports}}'],
            capture_output=True,
            text=True,
        )
        used_ports: set[int] = set()
        for line in result.stdout.splitlines():
            match = re.search(r':(\d+)->', line)
            if match:
                used_ports.add(int(match.group(1)))
        for port in range(self._port_range_start, self._port_range_end + 1):
            if port not in used_ports:
                return port
        raise NoPortsAvailable(
            f'no available ports in range {self._port_range_start}-{self._port_range_end}'
        )

    @staticmethod
    def _container_name(task_id: str) -> str:
        return f'openhands-{normalized_text(task_id)}'
