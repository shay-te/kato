"""Can kato reach a git host with the settings on the form?

The Git provider tab's "Test connection". A git host has no "who am I" kato
asks, so the host's own pull request API is asked for a few of the repositories
kato knows there — the very calls kato makes before it opens a pull request
(``_prepare_pull_request_api``, then the client's ``validate_connection``). A
refused token, a wrong API address or a missing Bitbucket email comes back in
the host's own words, per repository.

It runs in a child process whose environment is the form's settings, like the
ticket-system check (tracker_connection_check.py): building kato's config and
repository service reads the environment, and the running server's own must
not change under it.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

CHECK_TIMEOUT_SECONDS = 90.0
MAX_REPOSITORIES = 3
_STDERR_TAIL_LINES = 8
_CONFIG_DIR = Path(__file__).resolve().parents[1] / 'config'


def check_git_host_connection(env: Mapping[str, str], host: str, *, timeout: float = CHECK_TIMEOUT_SECONDS) -> dict:
    """``{'ok', 'host', 'error', 'message', 'checked': [{repository, ok, error}]}``."""
    try:
        result = subprocess.run(
            [sys.executable, '-m', __name__, host],
            env=dict(env), capture_output=True, text=True, timeout=timeout,
            stdin=subprocess.DEVNULL, cwd=str(_CONFIG_DIR.parents[1]),
        )
    except subprocess.TimeoutExpired:
        return _failed(host, f'no answer within {int(timeout)} seconds — check the address and your network')
    for line in reversed(result.stdout.splitlines()):
        try:
            answer = json.loads(line)
        except ValueError:
            continue
        if isinstance(answer, dict) and 'ok' in answer:
            return answer
    tail = '\n'.join(result.stderr.strip().splitlines()[-_STDERR_TAIL_LINES:])
    return _failed(host, tail or f'the check stopped with exit code {result.returncode}')


def host_answer(repository_service, host: str) -> dict:
    """Ask ``host``'s pull request API for up to ``MAX_REPOSITORIES`` of the
    repositories kato knows there, in parallel."""
    from kato_core_lib.data_layers.service.connection_health import provider_label

    label = provider_label(host)
    try:
        repositories = repository_service._ensure_repositories()
    except Exception as exc:  # noqa: BLE001 - every failure is the answer
        return _failed(host, f'kato could not list your repositories: {_words(exc)}')
    on_host = [repository for repository in repositories if _host_of(repository_service, repository) == host]
    if not on_host:
        return _failed(host, f'kato found no repository on {label} in your repositories folder, so there is '
                             f'nothing to test the settings against yet.')
    picked = on_host[:MAX_REPOSITORIES]
    with ThreadPoolExecutor(max_workers=len(picked)) as pool:
        checked = list(pool.map(lambda repository: _check_one(repository_service, repository), picked))
    failed = [entry for entry in checked if not entry['ok']]
    names = ', '.join(entry['repository'] for entry in checked)
    return {
        'ok': not failed,
        'host': host,
        'checked': checked,
        'error': '\n'.join(f'{entry["repository"]}: {entry["error"]}' for entry in failed),
        'message': '' if failed else f'Connected — {label} answered for {names}.',
    }


def _check_one(repository_service, repository) -> dict:
    name = str(getattr(repository, 'id', '') or '')
    try:
        repository_service._prepare_pull_request_api(repository)
        repository_service._pull_request_data_access(repository).validate_connection()
    except Exception as exc:  # noqa: BLE001 - every failure is the answer
        return {'repository': name, 'ok': False, 'error': _words(exc)}
    return {'repository': name, 'ok': True, 'error': ''}


def _host_of(repository_service, repository) -> str:
    try:
        return repository_service._resolved_pull_request_provider(repository)
    except Exception:  # noqa: BLE001 - a repository kato cannot place is on no host
        return ''


def _words(exc: Exception) -> str:
    return str(exc).strip() or exc.__class__.__name__


def _failed(host: str, error: str) -> dict:
    return {'ok': False, 'host': host, 'error': error, 'message': '', 'checked': []}


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - runs as the child process
    from hydra import compose, initialize_config_dir

    from kato_core_lib.data_layers.service.repository_service import RepositoryService

    host = (argv if argv is not None else sys.argv[1:])[0]
    with initialize_config_dir(config_dir=str(_CONFIG_DIR), version_base=None):
        open_cfg = compose(config_name='kato_core_lib').kato
        answer = host_answer(RepositoryService(open_cfg, 1), host)
    print(json.dumps(answer))
    return 0


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main())
