"""Ask the Docker command line, bounded and with no terminal.

Every call has a time limit and no stdin (a command must never read the
operator's terminal, nor flash a console window on Windows). "Docker cannot be
asked" — not installed, its engine stopped, no answer in time — is
``DockerUnavailable``; a refusal to act on one container is that container's
problem, in Docker's own words.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

from container_core_lib.container_core_lib.errors import DockerUnavailable

TIMEOUT_SECONDS = 60.0
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


@dataclass(frozen=True)
class Answer(object):
    returncode: int
    stdout: str = ''
    stderr: str = ''


def run_docker(argv: list[str], timeout: float) -> Answer | None:
    """The finished command, or ``None`` when it could not run or finish."""
    try:
        done = subprocess.run(
            argv, stdin=subprocess.DEVNULL, capture_output=True, text=True,
            timeout=timeout, check=False, creationflags=NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return Answer(done.returncode, done.stdout or '', done.stderr or '')


class DockerCli(object):
    def __init__(
        self, binary: str = '', *, run: Callable[[list[str], float], Answer | None] = run_docker,
        timeout: float = TIMEOUT_SECONDS, which: Callable[[str], str | None] = shutil.which,
    ) -> None:
        self._binary = binary or which('docker') or ''
        self._run = run
        self._timeout = timeout

    def containers(self) -> list[dict]:
        """Every container Docker has, running or not, as ``docker inspect``
        describes it."""
        ids = self._ask('ps', '--all', '--quiet', '--no-trunc').split()
        if not ids:
            return []
        answer = self._call('inspect', *ids)
        # One removed between the two calls fails the inspect, which still
        # describes the rest.
        try:
            described = json.loads(answer.stdout or '[]')
        except ValueError as exc:
            raise DockerUnavailable(f'Docker answered with something unreadable: {exc}') from exc
        return [entry for entry in described if isinstance(entry, dict)] if isinstance(described, list) else []

    def stop(self, container_id: str) -> str:
        """'' once stopped, else why not."""
        return self._act('stop', container_id)

    def remove(self, container_id: str) -> str:
        """'' once removed with its anonymous volumes, else why not."""
        return self._act('rm', '--force', '--volumes', container_id)

    def project_networks(self, project: str) -> list[str]:
        return self._listed('network', project)

    def project_volumes(self, project: str) -> list[str]:
        return self._listed('volume', project)

    def remove_network(self, network: str) -> str:
        return self._act('network', 'rm', network)

    def remove_volume(self, volume: str) -> str:
        return self._act('volume', 'rm', volume)

    # ----- calls -----

    def _listed(self, kind: str, project: str) -> list[str]:
        """The networks / volumes Compose made for ``project`` (by its label)."""
        filtered = f'label=com.docker.compose.project={project}'
        return self._ask(kind, 'ls', '--format', '{{.Name}}', '--filter', filtered).split()

    def _ask(self, *args: str) -> str:
        answer = self._call(*args)
        if answer.returncode != 0:
            raise DockerUnavailable(_words(answer) or f'docker {args[0]} failed')
        return answer.stdout

    def _act(self, *args: str) -> str:
        answer = self._call(*args)
        return '' if answer.returncode == 0 else (_words(answer) or f'docker {" ".join(args)} failed')

    def _call(self, *args: str) -> Answer:
        if not self._binary:
            raise DockerUnavailable('Docker is not installed on this computer')
        answer = self._run([self._binary, *args], self._timeout)
        if answer is None:
            raise DockerUnavailable('Docker did not answer')
        return answer


def _words(answer: Answer) -> str:
    return (answer.stderr or answer.stdout).strip()
