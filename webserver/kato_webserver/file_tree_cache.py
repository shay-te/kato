"""The Files pane's tree, cached on the SERVER.

FEATURE: opening a task never waits on a git walk the server has already done.

``/api/sessions/<task>/files`` costs four git/disk passes per repository
(branch, tracked tree, conflicts, changed set) plus a walk of the task folder,
so a many-repo task sat on "Loading repos…" for seconds whenever the browser
had no copy of its own — a reload, a first open, or the browser reloading the
tab because the page used too much memory. The operator: "can the repo loading
and caching happen in the backend please?"

So the server keeps the last tree it built for each task — in memory, and on
disk when a directory is configured, so a kato restart keeps it too. A client
with nothing on screen asks for that copy (``?cached=1``), paints it at once,
then asks for a fresh build. Every other read builds fresh, exactly as before,
and refreshes the copy. The cache therefore never decides what is current; it
only fills the gap until the fresh answer lands.

The browser used to hold this copy itself (IndexedDB, up to 16 MB a task,
parsed back into memory on every reload). One copy on the server serves every
browser and costs the page nothing.

Best-effort throughout: an unreadable or unwritable copy degrades to "no copy",
which is the old behaviour — never an error on the read the operator is waiting
for.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from collections import OrderedDict
from pathlib import Path

from utils_core_lib.utils_core_lib.atomic_write import atomic_write_json

#: Added to a response served from the cache, so the client knows to follow up
#: with a fresh build. Never stored.
CACHE_HIT_KEY = 'cache_hit'

#: Trees kept in memory. Matches the browser's own retention of recently viewed
#: tasks; an older one is still answered from disk.
DEFAULT_MAX_IN_MEMORY = 16

_UNSAFE_FILENAME_CHARS = re.compile(r'[^A-Za-z0-9._-]')


def serialize_payload(payload: dict) -> bytes:
    """Compact JSON with sorted keys.

    Sorted so a tree restored from disk serializes byte-for-byte like the fresh
    build of the same tree: the client compares bytes to decide whether to
    re-render, and a key-order difference would repaint an unchanged tree.
    """
    return json.dumps(
        payload, separators=(',', ':'), sort_keys=True, ensure_ascii=False,
    ).encode('utf-8')


def _marked_as_cache_hit(body: bytes) -> bytes:
    # Spliced rather than re-serialized: a many-repo tree runs to megabytes, and
    # the whole point of this path is to answer without redoing work.
    marker = b'{"' + CACHE_HIT_KEY.encode('ascii') + b'":true'
    rest = body.strip()[1:]
    return marker + (rest if rest.startswith(b'}') else b',' + rest)


class FileTreeCache:
    """The last-built Files payload per task: memory first, then disk."""

    def __init__(
        self,
        directory: str | Path | None = None,
        *,
        max_in_memory: int = DEFAULT_MAX_IN_MEMORY,
        logger: logging.Logger | None = None,
    ) -> None:
        self._directory = Path(directory).expanduser() if directory else None
        self._max_in_memory = max(1, int(max_in_memory))
        self._bodies: OrderedDict[str, bytes] = OrderedDict()
        self._lock = threading.Lock()
        self._logger = logger or logging.getLogger(__name__)

    def cached_body(self, task_id: str) -> bytes | None:
        """The last tree stored for ``task_id``, marked as a cache hit, or ``None``."""
        body = self._from_memory(task_id)
        if body is None:
            body = self._from_disk(task_id)
            if body is None:
                return None
            self._keep(task_id, body)
        return _marked_as_cache_hit(body)

    def store(self, task_id: str, payload: dict) -> bytes:
        """Record a freshly built payload; return its serialized body."""
        body = serialize_payload(payload)
        unchanged = self._from_memory(task_id) == body
        self._keep(task_id, body)
        # The active task is rebuilt on every poll. Rewriting identical
        # megabytes each time would be pure disk churn.
        if not unchanged:
            self._to_disk(task_id, payload)
        return body

    def forget(self, task_id: str) -> None:
        """Drop the task's copy everywhere — the task itself is gone."""
        with self._lock:
            self._bodies.pop(task_id, None)
        path = self._path(task_id)
        if path is None:
            return
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            self._logger.warning('could not remove cached file tree %s: %s', path, exc)

    def _from_memory(self, task_id: str) -> bytes | None:
        with self._lock:
            body = self._bodies.get(task_id)
            if body is not None:
                self._bodies.move_to_end(task_id)
            return body

    def _keep(self, task_id: str, body: bytes) -> None:
        with self._lock:
            self._bodies[task_id] = body
            self._bodies.move_to_end(task_id)
            while len(self._bodies) > self._max_in_memory:
                self._bodies.popitem(last=False)

    def _path(self, task_id: str) -> Path | None:
        # A task id is not trusted as a path: anything but a plain name
        # character is flattened, so no id can reach outside the directory.
        name = _UNSAFE_FILENAME_CHARS.sub('_', str(task_id or ''))
        if self._directory is None or not name.strip('._'):
            return None
        return self._directory / f'{name}.json'

    def _from_disk(self, task_id: str) -> bytes | None:
        path = self._path(task_id)
        if path is None or not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            self._logger.warning('ignoring unreadable cached file tree %s: %s', path, exc)
            return None
        return serialize_payload(payload) if isinstance(payload, dict) else None

    def _to_disk(self, task_id: str, payload: dict) -> None:
        path = self._path(task_id)
        if path is None:
            return
        # No fsync: a lost copy costs one slow load, and the file is rewritten
        # whenever the tree changes.
        atomic_write_json(
            path, payload, logger=self._logger, label='file tree cache', fsync=False,
        )
