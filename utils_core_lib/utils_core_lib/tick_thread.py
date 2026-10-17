"""Run a function every few seconds on a daemon thread, and stop it cleanly.

One implementation of the "tick" loop background workers keep needing: an
exception in one tick is logged and the next tick still runs, and ``stop``
wakes the thread at once instead of waiting out the interval.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable


class TickThread(object):
    def __init__(
        self,
        tick: Callable[[], None],
        *,
        interval_seconds: float,
        name: str,
        logger: logging.Logger | None = None,
    ) -> None:
        self._tick = tick
        self._interval = max(0.01, float(interval_seconds))
        self._name = name
        self._logger = logger or logging.getLogger(name)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def start(self) -> bool:
        """Start ticking; False when it is already running."""
        with self._lock:
            if self.running:
                return False
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name=self._name, daemon=True)
            self._thread.start()
            return True

    def stop(self, timeout: float = 5.0) -> None:
        with self._lock:
            thread = self._thread
            self._stop.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:
                self._logger.exception('%s: a tick failed; the next one still runs', self._name)
            self._stop.wait(self._interval)
