"""Atomic text write helper.

Counterpart to ``atomic_json_utils`` for plain-text payloads (lessons
files, notes, anything that isn't JSON). Same crash-safety: write to a
sibling tempfile, rename over the target. Concurrent writers all rename
into place; readers never see a half-written file.

Used by the lessons subsystem where parallel review-fix workers, the
"done" extractor, and the periodic compact job all touch the same
files. POSIX rename is atomic on the same filesystem.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path


def atomic_write_text(
    path: Path,
    content: str,
    *,
    encoding: str = 'utf-8',
    logger: logging.Logger | None = None,
    label: str = '',
) -> bool:
    """Write ``content`` to ``path`` atomically.

    Creates parent directories as needed. Returns True on success, False
    if the write failed (the previous file, if any, is preserved). When
    ``logger`` is provided, an OSError is logged at WARNING with
    ``label`` woven in so operators can tell which subsystem missed.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _log_failure(logger, label, path, exc)
        return False
    fd = -1
    tmp_path: str | None = None
    try:
        fd, tmp_path = tempfile.mkstemp(
            prefix=path.name + '.',
            suffix='.tmp',
            dir=str(path.parent),
        )
        with os.fdopen(fd, 'w', encoding=encoding) as fh:
            fd = -1  # ownership transferred to fh.close()
            fh.write(content)
        os.replace(tmp_path, path)
        tmp_path = None
        return True
    except OSError as exc:
        _log_failure(logger, label, path, exc)
        return False
    finally:
        if fd != -1:
            try:
                os.close(fd)
            except OSError:
                pass
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _log_failure(
    logger: logging.Logger | None,
    label: str,
    path: Path,
    exc: OSError,
) -> None:
    if logger is None:
        return
    label_text = f' for {label}' if label else ''
    logger.warning(
        'failed to write text%s at %s: %s', label_text, path, exc,
    )
