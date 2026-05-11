"""Atomic JSON file writer.

Workspace metadata is read from disk by the same process that writes
it (and sometimes by sibling processes — multiple workers reading the
tab list, the UI listing workspaces). A torn write would surface as
``json.JSONDecodeError`` at read time and block the operator.

The pattern: write to a temp file in the same directory, fsync it,
then ``os.replace`` over the destination. ``os.replace`` is atomic on
both POSIX and Windows. Reads always see either the old contents or
the fully-written new contents — never a half-written file.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Mapping


def atomic_write_json(
    path: str | os.PathLike[str],
    payload: Mapping[str, object],
    *,
    logger: logging.Logger | None = None,
    label: str = 'json file',
) -> None:
    """Write ``payload`` to ``path`` atomically.

    The destination is replaced with the new contents in a single
    syscall after the data is fully on disk. ``logger`` and ``label``
    are used only for warning text on failure paths; both are optional
    so this helper is safe to call from any layer.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, indent=2, sort_keys=True)
    fd = -1
    tmp_path = ''
    try:
        fd, tmp_path = tempfile.mkstemp(
            prefix=target.name + '.',
            suffix='.tmp',
            dir=str(target.parent),
        )
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            fd = -1
            fh.write(serialized)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, target)
        tmp_path = ''
    except OSError as exc:
        if logger is not None:
            logger.warning('failed to write %s at %s: %s', label, target, exc)
        raise
    finally:
        if fd != -1:
            try:
                os.close(fd)
            except OSError:
                pass
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
