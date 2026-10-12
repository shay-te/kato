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
import time
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


def run_all_with_deadline(
    works: list[Callable[[], Any]],
    *,
    seconds: float,
    default: Any = None,
) -> list[Any]:
    """Run every ``work`` at once; return each result in order, or ``default``
    for one that outran the shared ``seconds`` (or raised).

    The whole call takes at most ``seconds``, however many there are — a
    slow one only costs its own answer. Abandons what is late, exactly as
    :func:`run_with_deadline` does.
    """
    boxes: list[dict[str, Any]] = [{} for _ in works]

    def _run(work: Callable[[], Any], box: dict[str, Any]) -> None:
        try:
            box['value'] = work()
        except Exception:
            box['failed'] = True

    workers = [
        threading.Thread(target=_run, args=(work, box), daemon=True, name='deadline-probe')
        for work, box in zip(works, boxes)
    ]
    for worker in workers:
        worker.start()
    deadline = time.monotonic() + max(0.0, float(seconds))
    for worker in workers:
        worker.join(timeout=max(0.0, deadline - time.monotonic()))
    return [
        default if worker.is_alive() else box.get('value', default)
        for worker, box in zip(workers, boxes)
    ]

