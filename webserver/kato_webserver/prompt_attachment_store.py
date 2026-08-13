"""Large-file composer attachments, saved into the task workspace.

Small text files are inlined into the prompt as a fenced block — cheap, and
the agent sees the content directly. That stops working as the file grows: a
multi-megabyte log inlined into a prompt is mostly wasted context, and the UI
used to silently truncate it, so the operator handed over a log whose
interesting part had been cut off with only a toast to say so.

Past a size threshold the file is written HERE instead and the prompt carries
its path. The agent then reads or greps it with its own tools, at whatever
granularity the question needs, with nothing truncated.

Files land in ``<workspace>/attachments/``. That is the task folder — the
clone's PARENT, and the agent's ``--add-dir`` scope — so:

* the agent can read them, and
* git cannot stage them, because they are outside every worktree. The same
  reasoning puts ``pr_description.md`` there (see
  ``RepositoryService._pr_description_from_task_folder``); an attachment
  dropped inside a clone would otherwise land in the operator's next commit.

Names are sanitised to a bare filename before use: an upload is operator
input, and ``../`` in a name would otherwise write anywhere on disk.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from kato_core_lib.helpers.logging_utils import configure_logger

ATTACHMENTS_DIRNAME = 'attachments'

# Enough for the logs and dumps operators actually hand over, while still
# refusing something that would fill the disk.
MAX_ATTACHMENT_BYTES = 64 * 1024 * 1024

_logger = configure_logger('PromptAttachmentStore')

# Anything outside this set becomes '-'. Deliberately strict: the result is
# used as a path segment and is echoed back into the prompt.
_UNSAFE_CHARS = re.compile(r'[^A-Za-z0-9._-]+')


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
        return 'attachment.txt'
    cleaned = _UNSAFE_CHARS.sub('-', base).strip('-')
    if not cleaned or set(cleaned) <= {'.'}:
        return 'attachment.txt'
    # Long names are a filesystem problem, not a security one; keep the tail
    # so the extension survives.
    return cleaned[-120:]


def attachments_dir(workspace_dir) -> Path:
    return Path(workspace_dir) / ATTACHMENTS_DIRNAME


def _unique_path(directory: Path, name: str) -> Path:
    """``name``, or ``name-2``/``name-3``… when it is already taken.

    Attaching two files called ``logs.txt`` in one session must not have the
    second silently replace the first — the prompt would then reference a
    path whose contents are not what the operator attached.
    """
    candidate = directory / name
    if not candidate.exists():
        return candidate
    stem, extension = os.path.splitext(name)
    for index in range(2, 1000):
        candidate = directory / f'{stem}-{index}{extension}'
        if not candidate.exists():
            return candidate
    return directory / f'{stem}-{os.getpid()}{extension}'


def save_attachment(workspace_dir, name: str, data: bytes) -> dict:
    """Write ``data`` into the task's attachments folder.

    Returns ``{'ok': True, 'name', 'path', 'bytes'}`` on success, or
    ``{'ok': False, 'error'}`` — never raises, so a failed attachment leaves
    the composer usable.
    """
    if not workspace_dir:
        return {'ok': False, 'error': 'no workspace for this task'}
    payload = data or b''
    if len(payload) > MAX_ATTACHMENT_BYTES:
        megabytes = MAX_ATTACHMENT_BYTES // (1024 * 1024)
        return {'ok': False, 'error': f'file is larger than {megabytes} MB'}
    if not payload:
        return {'ok': False, 'error': 'file is empty'}
    directory = attachments_dir(workspace_dir)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        target = _unique_path(directory, safe_attachment_name(name))
        target.write_bytes(payload)
    except Exception as error:  # noqa: BLE001 - surfaced to the operator
        _logger.exception('failed to save attachment %s', name)
        return {'ok': False, 'error': str(error) or 'could not save the file'}
    return {
        'ok': True,
        'name': target.name,
        'path': str(target),
        'bytes': len(payload),
    }
