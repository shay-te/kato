"""Read and change the release-notes file safely.

One job: a read-modify-write that two writers can never interleave (a
cross-process lock held around BOTH halves) and a reader never sees half of
(written to a temporary file and moved into place).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from utils_core_lib.utils_core_lib.file_lock import exclusive_file_lock

from kato_core_lib.helpers.atomic_text_utils import atomic_write_text


def read_release_notes(path: Path) -> str:
    """The file's text, or '' when it does not exist (or cannot be read)."""
    try:
        return Path(path).read_text(encoding='utf-8')
    except OSError:
        return ''


def update_release_notes(
    path: Path, transform: Callable[[str], str], *, logger: logging.Logger | None = None,
) -> bool:
    """Apply ``transform`` to the file under its lock. True when the file changed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_file_lock(path):
        before = read_release_notes(path)
        after = transform(before)
        if after == before:
            return False
        return atomic_write_text(path, after, logger=logger, label='release notes')
