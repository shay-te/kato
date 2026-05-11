"""Cross-platform replacement for ``clean.sh``.

Tears down the Docker stack and prunes Docker resources. The original
script ran ``sudo docker system prune --all`` which only made sense on
Linux; on macOS / Windows Docker Desktop runs without sudo. This version
drops the implicit sudo and works the same on every platform that has
Docker installed.

Usage:
    python scripts/clean.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _script_utils import REPO_ROOT, have_executable  # noqa: E402


def _run(label: str, command: list[str], *, check: bool = True) -> None:
    print(f'==> {label}', flush=True)
    completed = subprocess.run(command, cwd=REPO_ROOT)
    if check and completed.returncode != 0:
        print(f'Step failed: {label} (exit {completed.returncode})', file=sys.stderr)
        sys.exit(completed.returncode)


def main() -> int:
    if not have_executable('docker'):
        print('docker is not on PATH; nothing to clean.', file=sys.stderr)
        return 1
    _run('docker compose down', ['docker', 'compose', 'down', '--remove-orphans', '--volumes'])
    container_ids = subprocess.run(
        ['docker', 'ps', '-aq'], capture_output=True, text=True, cwd=REPO_ROOT,
    ).stdout.split()
    if container_ids:
        _run('remove all containers', ['docker', 'rm', '-f', *container_ids])
    _run(
        'docker system prune (all + volumes)',
        ['docker', 'system', 'prune', '--all', '--volumes', '--force'],
    )
    docker_data = REPO_ROOT / os.environ.get('MOUNT_DOCKER_DATA_ROOT', './mount_docker_data')
    for target in (docker_data, REPO_ROOT / 'docker_data'):
        if target.exists():
            print(f'==> remove {target}', flush=True)
            shutil.rmtree(target, ignore_errors=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
