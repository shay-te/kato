"""A tamper-evident, append-only JSONL log with a per-line hash chain.

Each appended entry embeds ``prev_hash`` = ``sha256`` of the previous
line's raw bytes, so editing or deleting any line invalidates every line
after it. Operators can verify offline with ``sha256sum`` per line. Writes
are serialised with an exclusive file lock so parallel appenders never
compute their chain link against a stale predecessor.

This mirrors the audit-chain technique in ``sandbox_core_lib``'s sandbox
spawn log. The CHAIN is reimplemented here (not imported) because a black-box
lib must not depend on ``kato_core_lib``; this generic helper lives in kato so
kato-side logs (the Action Guard audit) can reuse one implementation. The LOCK
is shared — see ``utils_core_lib.file_lock`` for why a Windows no-op is not an
acceptable degradation for a chain whose only value is being verifiable.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from utils_core_lib.utils_core_lib.file_lock import exclusive_file_lock

GENESIS_HASH = '0' * 64


def last_chain_hash(path: Path) -> str:
    """``sha256`` of the log's last non-blank line, or the genesis hash.

    Read the tail only. Callers needing read+write atomicity across
    parallel appenders must hold :func:`exclusive_file_lock` around this
    AND the subsequent write (:func:`append_chained` does).
    """
    if not path.exists():
        return GENESIS_HASH
    try:
        with path.open('rb') as fh:
            try:
                fh.seek(-4096, os.SEEK_END)
            except OSError:
                fh.seek(0)
            tail = fh.read()
    except OSError:
        return GENESIS_HASH
    lines = [ln for ln in tail.splitlines() if ln.strip()]
    if not lines:
        return GENESIS_HASH
    return hashlib.sha256(lines[-1]).hexdigest()


def append_chained(path: Path, entry: dict) -> dict:
    """Append ``entry`` as one JSON line with its ``prev_hash`` link set.

    Returns the written entry (including ``prev_hash``). Atomic across
    parallel appenders via the exclusive lock; fsync'd to disk.
    """
    path = Path(path)
    with exclusive_file_lock(path):
        record = dict(entry)
        record['prev_hash'] = last_chain_hash(path)
        line = json.dumps(record, sort_keys=True) + '\n'
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, line.encode('utf-8'))
            os.fsync(fd)
        finally:
            os.close(fd)
    return record


def read_entries(path: Path, limit: int | None = None) -> list[dict]:
    """Parse the log into a list of entries (oldest first). Best-effort:
    unreadable / malformed lines are skipped, never raised."""
    path = Path(path)
    if not path.exists():
        return []
    entries: list[dict] = []
    try:
        with path.open('r', encoding='utf-8') as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except (ValueError, TypeError):
                    continue
    except OSError:
        return []
    if limit is not None and limit >= 0:
        return entries[-limit:]
    return entries


def verify_chain(path: Path) -> tuple[bool, int]:
    """Recompute the chain. Returns ``(ok, first_bad_line_index)``.

    ``first_bad_line_index`` is ``-1`` when intact. A line is bad when its
    stored ``prev_hash`` does not match the sha256 of the actual previous
    raw line — i.e. an earlier line was edited or removed.
    """
    path = Path(path)
    if not path.exists():
        return True, -1
    try:
        with path.open('rb') as fh:
            raw_lines = [ln for ln in fh.read().splitlines() if ln.strip()]
    except OSError:
        return False, 0
    prev = GENESIS_HASH
    for index, raw in enumerate(raw_lines):
        try:
            entry = json.loads(raw)
        except (ValueError, TypeError):
            return False, index
        if str(entry.get('prev_hash', '')) != prev:
            return False, index
        prev = hashlib.sha256(raw).hexdigest()
    return True, -1
