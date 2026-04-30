"""Live status feed shared between the kato process and the planning UI.

Captures every ``logger.info``/``logger.warning``/``logger.error`` call into
a thread-safe ring buffer and lets subscribers (the Flask SSE endpoint)
follow the stream in real time. The kato terminal still gets its normal
logging output — we only attach an additional handler.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass


_DEFAULT_CAPACITY = 500


@dataclass(frozen=True)
class StatusEntry(object):
    """A single line in the live status feed."""

    sequence: int
    epoch: float
    level: str
    logger: str
    message: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class StatusBroadcaster(object):
    """Thread-safe ring buffer + condition variable for live status events.

    Producers (the kato logging handler) call :meth:`publish`; consumers
    (the SSE generator in the webserver) call :meth:`recent` for the
    backlog and :meth:`wait_for_new` to block until the next entry
    arrives. The condition is a single shared object so a single
    ``notify_all`` wakes every waiter; we never spin.
    """

    def __init__(self, *, capacity: int = _DEFAULT_CAPACITY) -> None:
        self._capacity = max(1, int(capacity))
        self._buffer: deque[StatusEntry] = deque(maxlen=self._capacity)
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._sequence = 0

    def publish(self, *, level: str, logger_name: str, message: str) -> StatusEntry:
        normalized = str(message or '').strip()
        if not normalized:
            return _NULL_ENTRY
        with self._condition:
            self._sequence += 1
            entry = StatusEntry(
                sequence=self._sequence,
                epoch=time.time(),
                level=str(level or 'INFO').upper(),
                logger=str(logger_name or ''),
                message=normalized,
            )
            self._buffer.append(entry)
            self._condition.notify_all()
            return entry

    def recent(self, *, since_sequence: int = 0) -> list[StatusEntry]:
        """Snapshot of buffered entries with sequence > ``since_sequence``."""
        with self._lock:
            if since_sequence <= 0:
                return list(self._buffer)
            return [entry for entry in self._buffer if entry.sequence > since_sequence]

    def latest_sequence(self) -> int:
        with self._lock:
            return self._sequence

    def wait_for_new(self, *, since_sequence: int, timeout: float) -> list[StatusEntry]:
        """Block up to ``timeout`` seconds for entries newer than ``since_sequence``.

        Returns the new entries (may be empty if the timeout expired). Used
        by SSE generators to long-poll without spinning.
        """
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._condition:
            while self._sequence <= since_sequence:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return []
                self._condition.wait(timeout=remaining)
            if since_sequence <= 0:
                return list(self._buffer)
            return [entry for entry in self._buffer if entry.sequence > since_sequence]


_NULL_ENTRY = StatusEntry(sequence=0, epoch=0.0, level='', logger='', message='')


class StatusBroadcastHandler(logging.Handler):
    """Forwards log records into a :class:`StatusBroadcaster`.

    Attached to the root logger so every kato service's INFO+ messages
    flow through. We don't pre-format with timestamps / levels because
    the UI renders those on the client side.
    """

    def __init__(self, broadcaster: StatusBroadcaster, *, level: int = logging.INFO) -> None:
        super().__init__(level=level)
        self._broadcaster = broadcaster

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
        except Exception:
            self.handleError(record)
            return
        self._broadcaster.publish(
            level=record.levelname,
            logger_name=record.name,
            message=message,
        )


def install_status_broadcast_handler(
    broadcaster: StatusBroadcaster,
    *,
    level: int = logging.INFO,
    root_logger: logging.Logger | None = None,
) -> StatusBroadcastHandler:
    """Attach a :class:`StatusBroadcastHandler` to the root logger.

    Idempotent: re-installing replaces any prior handler bound to the
    same broadcaster so a hot-reload doesn't double-fire entries.
    """
    target = root_logger or logging.getLogger()
    for existing in list(target.handlers):
        if isinstance(existing, StatusBroadcastHandler) and existing._broadcaster is broadcaster:
            target.removeHandler(existing)
    handler = StatusBroadcastHandler(broadcaster, level=level)
    target.addHandler(handler)
    if target.level == logging.NOTSET or target.level > level:
        target.setLevel(level)
    return handler
