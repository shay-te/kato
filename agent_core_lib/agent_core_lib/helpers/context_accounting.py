"""Counting a turn's context, independently of whose CLI produced it.

Two rules that hold for every agent transport, however its usage payload is
spelled:

* **Prompt tokens are a guarded sum.** A usage payload is JSON from a
  subprocess: a key can be missing, ``null``, a float, or — in the one case
  that silently corrupts a total — a ``bool``, which Python would happily add
  as 1. Every provider needs the same guards; only the key NAMES differ, so
  the caller passes those.
* **A window the session already disproved was wrong.** A turn cannot use
  more prompt tokens than its window holds, so usage above the assumed limit
  proves the assumption, not the session, is wrong. Reporting "0% left" on it
  would be a false alarm on a perfectly healthy chat.

What stays with each transport: the key names, the model→window tables, and
the shape of its own stream events.
"""

from __future__ import annotations

from typing import Iterable


def sum_usage_tokens(usage: object, keys: Iterable[str]) -> int:
    """Total the ``keys`` in a CLI ``usage`` mapping; ``0`` for anything odd.

    Booleans are skipped rather than counted as 1, negatives cannot drag the
    total below zero, and a non-mapping payload is ``0`` — a wrong number here
    reaches the operator as a context reading they act on.
    """
    if not isinstance(usage, dict):
        return 0
    total = 0
    for key in keys:
        value = usage.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        total += int(value)
    return max(0, total)


def widen_window_to_observed(limit: object, used: object, *, ceiling: int) -> int:
    """Raise an assumed window that this session's own usage has disproved.

    Returns ``limit`` unchanged in the normal case — including an unknown
    ``0`` window, which must stay unknown rather than be invented from usage.
    When usage exceeds the limit, widen to ``ceiling`` (the largest window the
    transport ships) or to the observed usage itself when even that is
    exceeded, so the reading is never below what has already happened.
    """
    limit_tokens = _positive_int(limit)
    used_tokens = _positive_int(used)
    if limit_tokens <= 0 or used_tokens <= limit_tokens:
        return limit_tokens
    if used_tokens <= ceiling:
        return ceiling
    return used_tokens


def _positive_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value) if value > 0 else 0
