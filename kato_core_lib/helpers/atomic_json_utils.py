"""Atomic JSON write helper.

Several persistence paths in kato (workspace metadata, planning session
records, etc.) write a small JSON document to disk and need the same
crash-safety: write to a sibling tempfile, then rename over the target.
A partial write or process crash leaves the previous file intact rather
than a half-flushed JSON. Filesystem errors are logged-and-swallowed
because losing one persistence cycle should not tank the orchestrator.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any


_TMP_SUFFIX = '.json.tmp'


def atomic_write_json(
    path: Path,
    payload: Any,
    *,
    logger: logging.Logger | None = None,
    label: str = '',
) -> bool:
    """Write ``payload`` to ``path`` atomically as pretty-printed JSON.

    Returns True on success, False if the write failed (the previous
    file, if any, is preserved). When ``logger`` is provided, an
    OSError is logged at WARNING with ``label`` woven into the message
    so operators can tell which subsystem's persistence cycle missed.
    """
    tmp_path = path.with_suffix(_TMP_SUFFIX)
    try:
        tmp_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding='utf-8',
        )
        tmp_path.replace(path)
        return True
    except OSError as exc:
        if logger is not None:
            label_text = f' for {label}' if label else ''
            logger.warning(
                'failed to persist json%s at %s: %s', label_text, path, exc,
            )
        return False
