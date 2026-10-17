"""The one error the queue raises at its callers, with a kind a host can map."""

from __future__ import annotations

from enum import Enum


class ErrorKind(str, Enum):
    NOT_FOUND = 'not_found'      # no such plan / step
    CONFLICT = 'conflict'        # not allowed in the plan's / step's current state
    INVALID = 'invalid'          # the request itself is wrong (e.g. a bad split)
    UNAVAILABLE = 'unavailable'  # the host can't do this right now


class PlanQueueError(RuntimeError):
    """Refused; the message says why, for the operator."""

    def __init__(self, message: str, kind: ErrorKind = ErrorKind.CONFLICT) -> None:
        super().__init__(message)
        self.kind = kind
