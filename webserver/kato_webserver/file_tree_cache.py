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

Each entry also carries the two derived forms the route serves, computed once
per distinct tree rather than per request:

* an **ETag** (a hash of the bytes). The active task's tree is rebuilt on every
  poll and is almost always byte-identical to the last one; a client that
  sends the tag back gets a bodiless 304 instead of the whole tree again. On
  a 27-repository task that was 1.4 MB every five seconds, downloaded and
  parsed to be thrown away as unchanged.
* a **gzipped body**, for a client that accepts it. A tree is mostly repeated
  directory names, so it compresses roughly seven to one.

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

from kato_webserver.http_payload import TaggedPayload, serialize_payload
from utils_core_lib.utils_core_lib.atomic_write import atomic_write_json

#: Response header set (to ``hit``) on a tree served from the cache, so the
#: client knows to follow up with a fresh build. A header rather than a key
#: spliced into the body: the body stays byte-identical to the fresh build of
#: the same tree, so its ETag matches and the follow-up can be a 304.
CACHE_HIT_HEADER = 'X-Tree-Cache'
CACHE_HIT_VALUE = 'hit'

#: Trees kept in memory. Matches the browser's own retention of recently viewed
#: tasks; an older one is still answered from disk.
DEFAULT_MAX_IN_MEMORY = 16

_UNSAFE_FILENAME_CHARS = re.compile(r'[^A-Za-z0-9._-]')


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
        self._entries: OrderedDict[str, TaggedPayload] = OrderedDict()
        self._lock = threading.Lock()
        self._logger = logger or logging.getLogger(__name__)

    def cached(self, task_id: str) -> TaggedPayload | None:
        """The last tree stored for ``task_id``, or ``None``."""
        entry = self._from_memory(task_id)
        if entry is None:
            body = self._from_disk(task_id)
            if body is None:
                return None
            entry = TaggedPayload.from_body(body)
            self._keep(task_id, entry)
        return entry

    def store(self, task_id: str, payload: dict) -> TaggedPayload:
        """Record a freshly built payload; return it in its servable forms."""
        body = serialize_payload(payload)
        previous = self._from_memory(task_id)
        # The active task is rebuilt on every poll. Rehashing, recompressing
        # and rewriting identical megabytes each time would be pure churn.
        if previous is not None and previous.body == body:
            return previous
        entry = TaggedPayload.from_body(body)
        self._keep(task_id, entry)
        self._to_disk(task_id, payload)
        return entry

    def forget(self, task_id: str) -> None:
        """Drop the task's copy everywhere — the task itself is gone."""
        with self._lock:
            self._entries.pop(task_id, None)
        path = self._path(task_id)
        if path is None:
            return
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            self._logger.warning('could not remove cached file tree %s: %s', path, exc)

    def _from_memory(self, task_id: str) -> TaggedPayload | None:
        with self._lock:
            entry = self._entries.get(task_id)
            if entry is not None:
                self._entries.move_to_end(task_id)
            return entry

    def _keep(self, task_id: str, entry: TaggedPayload) -> None:
        with self._lock:
            self._entries[task_id] = entry
            self._entries.move_to_end(task_id)
            while len(self._entries) > self._max_in_memory:
                self._entries.popitem(last=False)

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
