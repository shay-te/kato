"""Process-local cache for derived results of reading a file on disk.

Several spawn-time helpers (architecture-doc directive, lessons body,
…) re-read the same files on every Claude turn. The files barely
change but the cost was real — full ``read_text`` plus template
formatting, repeated thousands of times an hour during active
operator work.

The shared pattern: key the cache on ``(path, mtime, size)``, return
the cached value when the stat tuple is unchanged, recompute and
cache when either changes. Mtime + size catches every realistic
edit (touch + truncate, in-place rewrite, length-preserving edit
all bump at least one). Process-local because we want zero-config
invalidation on operator restart, not a long-lived cache to manage.

This module owns the locking and the cache map; callers supply only
``path`` and a ``compute(path)`` function. That keeps the cache
behaviour identical across consumers — which it wasn't, before this
extraction: each callsite hand-rolled slightly different stat /
``is_file`` / lock idioms.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable


def stat_keyed_cache():
    """Build a fresh ``(load, invalidate)`` pair for a single cache.

    Each caller (``architecture_doc_utils``, ``lessons_doc_utils``,
    future readers) gets its own isolated cache so one consumer's
    invalidation never wipes another's entries.

    Usage:

        _CACHE_LOAD, _CACHE_RESET = stat_keyed_cache()

        def read_thing(path: str) -> str:
            return _CACHE_LOAD(path, _compute)

    ``compute(file_path)`` is invoked only on cache miss and only
    after the path has been validated as a regular file. It receives
    a ``Path`` whose ``stat()`` already succeeded, so callers don't
    need to re-stat or guard against missing-file races inside the
    compute callback.

    Returns:
        load(path, compute) -> str | None
            ``None`` when ``path`` is empty / not a regular file /
            unreadable. Otherwise the cached or freshly-computed
            value from ``compute``.
        reset() -> None
            Drop every cached entry. Tests use this to keep cases
            isolated; production code does not need it.
    """
    cache: dict[str, tuple[float, int, str]] = {}
    lock = threading.Lock()

    def load(path: str, compute: Callable[[Path], str]) -> str | None:
        normalized = str(path or '').strip()
        if not normalized:
            return None
        file_path = Path(normalized).expanduser()
        try:
            stat = file_path.stat()
            is_file = file_path.is_file()
        except (FileNotFoundError, OSError):
            return None
        if not is_file:
            return None
        cache_key = str(file_path)
        mtime = stat.st_mtime
        size = stat.st_size
        with lock:
            cached = cache.get(cache_key)
            if cached is not None and cached[0] == mtime and cached[1] == size:
                return cached[2]
        value = compute(file_path)
        with lock:
            cache[cache_key] = (mtime, size, value)
        return value

    def reset() -> None:
        with lock:
            cache.clear()

    return load, reset
