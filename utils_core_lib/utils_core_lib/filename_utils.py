"""Turning an untrusted name into a safe file on disk.

Two callers need exactly this, for the same reason and with the same stakes:

  * the composer's large-file attachments (an operator upload), and
  * ticket attachments downloaded from an issue tracker (a screenshot whose
    filename was typed by whoever attached it).

Both take a name from outside, use it as a path segment, and echo it back into
a prompt. A second copy of this logic is how one of them ends up with weaker
rules than the other, so it lives here — the shared-primitives lib any lib may
import — rather than being re-derived per caller.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

#: Anything outside this set becomes '-'. Deliberately strict: the result is
#: used as a path segment and is echoed back into the prompt.
_UNSAFE_CHARS = re.compile(r'[^A-Za-z0-9._-]+')

#: What an unusable name (blank, or dots only) becomes.
_PLACEHOLDER_NAME = 'attachment.txt'

#: Long names are a filesystem problem, not a security one.
_MAX_NAME_CHARS = 120


def safe_attachment_name(name: str) -> str:
    """A bare, filesystem-safe filename — never a path.

    ``os.path.basename`` alone is not enough on a POSIX server receiving a
    Windows-style ``..\\..\\etc\\passwd``: the backslashes are ordinary
    characters there, so basename returns the whole string. Both separators
    are stripped first, then everything outside the allowlist collapses.
    """
    raw = str(name or '').replace('\\', '/')
    base = os.path.basename(raw).strip()
    # A name of dots only ('.', '..') resolves to a directory, not a file.
    if not base or set(base) <= {'.'}:
        return _PLACEHOLDER_NAME
    cleaned = _UNSAFE_CHARS.sub('-', base).strip('-')
    if not cleaned or set(cleaned) <= {'.'}:
        return _PLACEHOLDER_NAME
    # Keep the TAIL so the extension survives — a truncated head still names
    # the file, but losing ``.png`` changes what it is.
    return cleaned[-_MAX_NAME_CHARS:]


def unique_file_path(directory: Path, name: str) -> Path:
    """``name``, or ``name-2``/``name-3``… when it is already taken.

    Two files called ``logs.txt`` must not have the second silently replace
    the first — whatever referenced the path would then point at contents
    that are not what was attached.
    """
    for candidate in _candidate_paths(directory, name):
        if not candidate.exists():
            return candidate
    return candidate  # every slot taken: the last resort


def content_file_path(
    directory: Path, name: str, content: bytes, *, claimed=(),
) -> tuple[Path, bool]:
    """Where ``content`` called ``name`` belongs: ``(path, already_saved)``.

    The file that already holds exactly these bytes (``True`` — nothing to
    write), else the first free slot ``unique_file_path`` would pick
    (``False``). Saving the same download twice then lands on the same file,
    instead of ``bug.png`` and ``bug-2.png`` holding one screenshot twice —
    while a DIFFERENT file of the same name is still never overwritten.

    ``claimed``: paths the caller already used in this batch. Two attachments
    with the same name and the same bytes are still two attachments, so a
    claimed path is never handed out again.
    """
    taken = {str(path) for path in claimed}
    for candidate in _candidate_paths(directory, name):
        if str(candidate) in taken:
            continue
        if not candidate.exists():
            return candidate, False
        if _holds(candidate, content):
            return candidate, True
    return candidate, False


def _candidate_paths(directory: Path, name: str):
    """``name``, ``name-2`` … ``name-999``, then a last resort no one else picks."""
    yield Path(directory) / name
    stem, extension = os.path.splitext(name)
    for index in range(2, 1000):
        yield Path(directory) / f'{stem}-{index}{extension}'
    yield Path(directory) / f'{stem}-{os.getpid()}{extension}'


def _holds(path: Path, content: bytes) -> bool:
    """Whether ``path`` is a file with exactly ``content``. Unreadable is no."""
    try:
        return path.is_file() and path.stat().st_size == len(content) and (
            path.read_bytes() == content
        )
    except OSError:
        return False
