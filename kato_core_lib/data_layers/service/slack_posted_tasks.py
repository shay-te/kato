"""The tasks already posted to Slack, so each is announced once.

A task is posted when the operator presses Done (it goes to In Review with its
pull requests) — and reaching the tracker's Done later must not post it again.
Kept in ``~/.kato/slack_posted_tasks.json`` (``KATO_SLACK_POSTED_PATH``),
task ids lower-cased.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from utils_core_lib.utils_core_lib.atomic_write import atomic_write_json

from kato_core_lib.helpers.kato_paths_utils import kato_home_path

POSTED_PATH_ENV_KEY = 'KATO_SLACK_POSTED_PATH'


class SlackPostedTasks(object):
    def __init__(self, path: str | Path | None = None, *, clock=time.time) -> None:
        self._path = Path(path) if path else kato_home_path('slack_posted_tasks.json', env_key=POSTED_PATH_ENV_KEY)
        self._clock = clock
        self._lock = threading.Lock()

    def has(self, task_id: str) -> bool:
        return _key(task_id) in self._read()

    def mark(self, task_id: str) -> None:
        with self._lock:
            posted = self._read()
            posted[_key(task_id)] = self._clock()
            atomic_write_json(self._path, posted, label='tasks posted to Slack')

    def _read(self) -> dict:
        try:
            data = json.loads(self._path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}


def _key(task_id: object) -> str:
    return str(task_id or '').strip().lower()
