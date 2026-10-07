"""Has a file changed since it was last read? Answered right on every filesystem.

A cache that re-reads a file only when its modification time moves goes stale
on Linux. File times there come from a coarse kernel clock — one tick is a few
milliseconds — so a rewrite inside the same tick as the cached read leaves
``st_mtime_ns`` exactly as it was, and the cache keeps serving the old content.
macOS stamps real nanoseconds, which is why it never shows there. (Git calls
this the "racy" problem.) Measured: a store read straight after another
instance wrote it returned the previous status 37 times out of 40 on Linux.

Two defences, both needed:

* the signature carries the size and the inode as well as the time. A write
  through ``os.replace`` always lands a new inode, so the usual rewrite is
  caught even within one tick;
* a read taken while the file is still FRESH (modified less than
  ``RACY_WINDOW_NS`` ago) is never served again from a cache: an in-place
  rewrite of the same size in the same tick would still match the signature,
  so the next read goes back to the disk. Once the file has been quiet for the
  window, its signature is trustworthy.
"""

from __future__ import annotations

import os
import time
from typing import NamedTuple

# Covers the coarsest clocks a store may sit on (FAT keeps two seconds).
RACY_WINDOW_NS = 2_000_000_000


class FileSignature(NamedTuple):
    mtime_ns: int
    size: int
    inode: int


def file_signature(path: str | os.PathLike) -> FileSignature | None:
    """``(mtime_ns, size, inode)`` of ``path``; ``None`` when it can't be stat'ed."""
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return FileSignature(stat.st_mtime_ns, stat.st_size, stat.st_ino)


def trustworthy(signature: FileSignature | None, *, now_ns: int | None = None) -> bool:
    """Whether what was read at ``signature`` may be served from a cache later.

    A missing file is: creating it changes the signature. A file modified within
    the racy window is not — see the module docstring.
    """
    if signature is None:
        return True
    now = time.time_ns() if now_ns is None else now_ns
    return now - signature.mtime_ns >= RACY_WINDOW_NS
