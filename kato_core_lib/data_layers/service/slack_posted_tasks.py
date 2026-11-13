"""The tasks already posted to Slack, and where each stood when it was.

A task is posted when the operator presses Done (it goes to In Review with its
pull requests) — and reaching the tracker's Done later must not post it again,
unless something was committed in it since. So each post keeps the commit
every repository of the task was on (``TaskCommits``).

Kept in ``~/.kato/slack_posted_tasks.json`` (``KATO_SLACK_POSTED_PATH``),
task ids lower-cased: ``{"una-1": {"at": <epoch>, "commits": {"backend":
"<sha>"}}}``. An entry written before commits were kept is a bare epoch; it
reads back with ``commits`` None, as does a post whose commits could not be
read.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from utils_core_lib.utils_core_lib.atomic_write import atomic_write_json

from kato_core_lib.helpers.kato_paths_utils import kato_home_path

POSTED_PATH_ENV_KEY = 'KATO_SLACK_POSTED_PATH'


@dataclass(frozen=True)
class SlackPost(object):
    """When a task was last posted, and each repository's commit then
    (None: not known — see the module docstring)."""

    at: float
    commits: dict[str, str] | None = None


class SlackPostedTasks(object):
    def __init__(self, path: str | Path | None = None, *, clock=time.time) -> None:
        self._path = Path(path) if path else kato_home_path('slack_posted_tasks.json', env_key=POSTED_PATH_ENV_KEY)
        self._clock = clock
        self._lock = threading.Lock()

    def last_post(self, task_id: str) -> SlackPost | None:
        """The task's last post, or None when it was never posted."""
        return _post_from(self._read().get(_key(task_id)))

    def mark(self, task_id: str, commits: dict[str, str] | None = None) -> None:
        entry: dict = {'at': self._clock()}
        if commits is not None:
            entry['commits'] = dict(commits)
        with self._lock:
            posted = self._read()
            posted[_key(task_id)] = entry
            atomic_write_json(self._path, posted, label='tasks posted to Slack')

    def _read(self) -> dict:
        try:
            data = json.loads(self._path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}


def _post_from(entry: object) -> SlackPost | None:
    if isinstance(entry, bool):
        return None
    if isinstance(entry, (int, float)):
        return SlackPost(at=float(entry))  # written before commits were kept
    if not isinstance(entry, dict):
        return None
    try:
        at = float(entry.get('at') or 0.0)
    except (TypeError, ValueError):
        at = 0.0
    commits = entry.get('commits')
    if not isinstance(commits, dict):
        return SlackPost(at=at)
    return SlackPost(at=at, commits={str(repo): str(commit) for repo, commit in commits.items()})


def _key(task_id: object) -> str:
    return str(task_id or '').strip().lower()
