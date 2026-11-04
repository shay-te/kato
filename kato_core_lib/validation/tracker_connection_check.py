"""Can kato reach the ticket system with the settings saved right now?

The setup wizard asks this the moment the ticket details are saved, so a wrong
token or address is reported on that step, in full — not after the whole
wizard, cut short, as "Start failed". It is kato's own check: the ticket client
kato polls with, and the query it validates at boot.

It runs in a child process whose environment is the saved settings. Building
kato's config reads the environment, and the running server's own must not
change under it: setup mode's loop snapshots and restores that environment
while it waits for the configuration to be complete.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

CHECK_TIMEOUT_SECONDS = 60.0
# A child that dies before answering says why on its last lines.
_STDERR_TAIL_LINES = 8
_CONFIG_DIR = Path(__file__).resolve().parents[1] / 'config'


def check_tracker_connection(env: Mapping[str, str], *, timeout: float = CHECK_TIMEOUT_SECONDS) -> dict:
    """``{'ok': bool, 'platform': str, 'error': str}`` for the ticket system
    the settings in ``env`` name (``ok`` with no error for no tracker)."""
    try:
        result = subprocess.run(
            [sys.executable, '-m', __name__],
            env=dict(env), capture_output=True, text=True, timeout=timeout,
            stdin=subprocess.DEVNULL, cwd=str(_CONFIG_DIR.parents[1]),
        )
    except subprocess.TimeoutExpired:
        return _failed(env, f'no answer within {int(timeout)} seconds — check the address and your network')
    for line in reversed(result.stdout.splitlines()):
        try:
            answer = json.loads(line)
        except ValueError:
            continue
        if isinstance(answer, dict) and 'ok' in answer:
            return {'ok': bool(answer['ok']), 'platform': str(answer.get('platform', '')),
                    'error': str(answer.get('error', ''))}
    tail = '\n'.join(result.stderr.strip().splitlines()[-_STDERR_TAIL_LINES:])
    return _failed(env, tail or f'the check stopped with exit code {result.returncode}')


def connection_problem(open_cfg) -> str:
    """'' when the ticket system answered kato's own validation query, else
    what went wrong. Nothing to reach without a tracker."""
    from kato_core_lib.data_layers.data_access.task_data_access import TaskDataAccess
    from kato_core_lib.data_layers.service.task_service import TaskService
    from kato_core_lib.helpers.issue_platform_utils import is_no_tracker
    from kato_core_lib.kato_core_lib import KatoCoreLib

    if is_no_tracker(open_cfg.issue_platform):
        return ''
    try:
        client, ticket_cfg = KatoCoreLib._build_ticket_client(open_cfg, 1)
        TaskService(ticket_cfg, TaskDataAccess(ticket_cfg, client)).validate_connection()
    except Exception as exc:  # noqa: BLE001 - every failure is the answer
        return str(exc) or exc.__class__.__name__
    return ''


def _failed(env: Mapping[str, str], error: str) -> dict:
    return {'ok': False, 'platform': _platform(env), 'error': error}


def _platform(env: Mapping[str, str]) -> str:
    return str(env.get('KATO_ISSUE_PLATFORM') or 'youtrack').strip().lower()


def main() -> int:
    from hydra import compose, initialize_config_dir

    with initialize_config_dir(config_dir=str(_CONFIG_DIR), version_base=None):
        open_cfg = compose(config_name='kato_core_lib').kato
        error = connection_problem(open_cfg)
    print(json.dumps({'ok': not error, 'platform': _platform(os.environ), 'error': error}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
