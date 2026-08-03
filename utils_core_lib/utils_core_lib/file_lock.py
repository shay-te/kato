"""Cross-process exclusive file locking — the canonical implementation.

Four modules grew their own copy of this, in TWO shapes, and the shapes were
not equivalent:

    site                                     POSIX          Windows
    comment_store                            fcntl.flock    msvcrt.locking
    repository_approval_service              fcntl.flock    msvcrt.locking
    hash_chain_log (audit chain)             fcntl.flock    NO-OP
    sandbox manager (audit chain + rate cap) fcntl.flock    NO-OP

The two no-op copies each documented the degradation as acceptable "because
Windows operators are single-process anyway". That reasoning does not survive
shipping a Windows desktop build: the thing they guard is an append-only
hash-chained audit log plus a spawn rate limit, and *the whole point of a hash
chain is that it is checkable*. Two concurrent appends without the lock each
read the same predecessor hash, so one entry's ``prev_hash`` points at a link
that is no longer the tail — the chain fails verification afterwards, and it
fails in a way indistinguishable from tampering. The rate limit degrades the
same way: both writers count ``N-1`` recent spawns and both admit.

The msvcrt path already existed, was already exercised on real Windows, and
costs nothing to reuse. So there is exactly one implementation now, and it
locks on every platform that offers a primitive.

The no-op is retained as the last rung only for a hypothetical platform with
neither module — never as the Windows answer.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

try:  # POSIX
    import fcntl  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover — Windows
    fcntl = None  # type: ignore[assignment]

try:  # Windows
    import msvcrt  # type: ignore[import-not-found]
except ImportError:  # POSIX
    msvcrt = None  # type: ignore[assignment]

# ``msvcrt.locking`` is byte-range. One byte at offset 0 is enough: nothing
# ever reads the lockfile's contents, it exists only to be locked.
_LOCK_BYTES = 1


@contextlib.contextmanager
def exclusive_file_lock(path: str | os.PathLike[str]):
    """Hold a cross-process exclusive lock on ``<path>.lock`` for the block.

    ``path`` is the file being protected, NOT the lockfile — the lockfile is a
    sibling with ``.lock`` appended, so the protected file can be replaced
    (``os.replace``) underneath the lock without the lock moving with it.

    Yields the lock file descriptor. Callers doing a read-modify-write should
    hold this around BOTH halves; taking it only around the write leaves the
    read racing.
    """
    lock_path = Path(str(path) + '.lock')
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    # 0600: a lockfile has no reason to be group- or world-accessible, and the
    # audit-chain callers rely on it.
    descriptor = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if fcntl is not None:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            try:
                yield descriptor
            finally:
                # The fd closes either way, so a failed unlock has no
                # operational consequence — don't mask the caller's exception.
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                except OSError:
                    pass
        elif msvcrt is not None:
            os.lseek(descriptor, 0, os.SEEK_SET)
            # LK_LOCK blocks for ~10s and then RAISES rather than waiting
            # longer, so a sibling holding the lock across a slow operation
            # surfaces as an exception unless we retry. Retry forever: the
            # alternative is a spurious failure on a store write that would
            # have succeeded a moment later.
            while True:
                try:
                    msvcrt.locking(descriptor, msvcrt.LK_LOCK, _LOCK_BYTES)
                    break
                except OSError:
                    continue
            try:
                yield descriptor
            finally:
                try:
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, _LOCK_BYTES)
                except OSError:
                    pass
        else:  # pragma: no cover — no platform ships without both
            yield descriptor
    finally:
        os.close(descriptor)
