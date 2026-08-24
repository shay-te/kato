"""Running best-effort work with a hard deadline.

For values that are nice to have but must never hold anything up — a UI hint,
an enrichment lookup, a liveness probe. The caller gets an answer within the
deadline or gets ``default``; it never waits on something it does not need.

The worker is a daemon thread and is NOT cancelled on timeout: Python cannot
interrupt a thread blocked in I/O. It is abandoned instead, which is the point
— whatever it is stuck on stops being the caller's problem. Use this only
where abandoning the work is safe (no partial writes, no held locks the caller
needs).
"""

from __future__ import annotations

import threading
from typing import Any, Callable


def run_with_deadline(
    work: Callable[[], Any],
    *,
    seconds: float,
    default: Any = None,
    on_timeout: Callable[[], None] | None = None,
) -> Any:
    """Return ``work()``'s result, or ``default`` if it outruns ``seconds``.

    An exception inside ``work`` also yields ``default`` — a best-effort value
    that failed is the same as one that never arrived, and raising would push
    the failure back onto the caller this exists to protect.
    """
    box: dict[str, Any] = {}

    def _target() -> None:
        try:
            box['value'] = work()
        except Exception:
            box['failed'] = True

    worker = threading.Thread(target=_target, daemon=True, name='deadline-probe')
    worker.start()
    worker.join(timeout=max(0.0, float(seconds)))
    if worker.is_alive():
        if on_timeout is not None:
            try:
                on_timeout()
            except Exception:
                pass
        return default
    return box.get('value', default)
