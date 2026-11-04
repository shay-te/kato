"""The agents to resume, on disk: ``~/.kato/resume_agents.json``
(``KATO_RESUME_AGENTS_PATH`` overrides it)."""

from __future__ import annotations

import json
import time
from pathlib import Path

from utils_core_lib.utils_core_lib.atomic_write import atomic_write_json

from kato_core_lib.helpers.kato_paths_utils import kato_home_path

RESUME_PATH_ENV_KEY = 'KATO_RESUME_AGENTS_PATH'


class ResumeStore(object):
    def __init__(self, path: str | Path | None = None, *, clock=time.time) -> None:
        self._path = Path(path) if path else kato_home_path('resume_agents.json', env_key=RESUME_PATH_ENV_KEY)
        self._clock = clock

    @property
    def path(self) -> Path:
        return self._path

    def read(self) -> list[str]:
        """The task ids written last ([] for none, or a file that cannot be read)."""
        try:
            data = json.loads(self._path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return []
        ids = data.get('task_ids') if isinstance(data, dict) else None
        if not isinstance(ids, list):
            return []
        return sorted({str(task_id).strip() for task_id in ids if isinstance(task_id, str) and task_id.strip()})

    def write(self, task_ids) -> bool:
        payload = {'task_ids': sorted(set(task_ids)), 'recorded_at': self._clock()}
        return atomic_write_json(self._path, payload, label='agents to resume')
