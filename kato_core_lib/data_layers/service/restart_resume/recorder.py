"""Keep the list of working agents on disk while kato runs.

Every few seconds the working agents are read and, when the set changed,
written. A graceful stop (Shutdown, Ctrl+C, SIGTERM) calls ``record_final``
before the agents are stopped: that writes the set once more and freezes the
recorder, so the teardown — every agent stopping — is never recorded as
"nothing was running". A kill that cannot be caught leaves the last write.

``pending``: agents remembered from the last run and not yet resumed. They are
kept in every write until the resumer has dealt with them, so a kato that stops
again during its own boot still remembers them.
"""

from __future__ import annotations

import threading

from kato_core_lib.data_layers.service.restart_resume.running_agents import running_task_ids
from kato_core_lib.helpers.late_binding import provider_for
from kato_core_lib.helpers.logging_utils import configure_logger

RECORD_INTERVAL_SECONDS = 5.0


class RunningAgentsRecorder(object):
    def __init__(
        self, *, session_manager, store, pending=(), interval: float = RECORD_INTERVAL_SECONDS, logger=None,
    ) -> None:
        self._get_session_manager = provider_for(session_manager)
        self._store = store
        self._interval = interval
        self.logger = logger or configure_logger('RunningAgentsRecorder')
        self._lock = threading.Lock()
        self._pending = set(pending)
        self._last: list[str] | None = None
        self._frozen = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def resumed(self, task_id: str) -> None:
        """A remembered agent was dealt with: no longer kept for its sake."""
        with self._lock:
            self._pending.discard(task_id)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name='running-agents-recorder', daemon=True)
        self._thread.start()

    def record(self) -> list[str]:
        """Write the working agents when they changed; the set written."""
        with self._lock:
            if self._frozen:
                return list(self._last or [])
            return self._write(force=False)

    def record_final(self) -> list[str]:
        """The last write, before kato stops its agents; nothing after it."""
        self._stop.set()
        with self._lock:
            ids = self._write(force=True) if not self._frozen else list(self._last or [])
            self._frozen = True
        return ids

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        # Every write guarded, the first included: one failing write must
        # never end the recording.
        while True:
            try:
                self.record()
            except Exception:
                self.logger.exception('could not record the working agents')
            if self._stop.wait(self._interval):
                return

    def _write(self, *, force: bool) -> list[str]:
        ids = sorted(set(running_task_ids(self._get_session_manager())) | self._pending)
        if force or ids != self._last:
            self._store.write(ids)
            self._last = ids
        return ids
