"""A split the operator already wrote as files, found in a task's folder.

A split folder sits directly in the task folder — never inside a repository
clone — and holds at least two markdown files whose names start with a number
(``00-fix-hash-endpoint-bugs.md``, ``01-client-realm.md``): one task per
numbered file, in number order. A ``README.md`` beside them is the split's
overview, read for the order and never made a task. Unnumbered files are left
out. Nothing here writes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from plan_queue_core_lib.plan_queue_core_lib.data.plan import SplitFile

_NUMBERED = re.compile(r'^(\d+)[^/\\]*\.md$', re.IGNORECASE)
OVERVIEW_NAME = 'readme.md'
MIN_SPLIT_FILES = 2
# Read cap per file. The queue refuses a task text over its own limit with a
# clear reason; this only keeps a stray huge file from being read at all.
MAX_FILE_BYTES = 256 * 1024


class SplitFolderError(ValueError):
    """The folder can't be used as a split; the message says why."""


@dataclass(frozen=True)
class SplitFolder(object):
    path: str                  # relative to the task folder, e.g. 'tasks'
    files: tuple[str, ...]     # the numbered files, in task order
    overview: str = ''         # the overview's file name, '' when there is none

    def to_dict(self) -> dict:
        return {'path': self.path, 'files': len(self.files), 'context': self.overview}


def find_split_folders(task_root: str | Path) -> list[SplitFolder]:
    """Every folder in the task folder that holds a split, by name."""
    root = Path(task_root)
    if not root.is_dir():
        return []
    found = []
    for child in sorted(root.iterdir(), key=lambda entry: entry.name.lower()):
        folder = _split_folder(root, child)
        if folder is not None:
            found.append(folder)
    return found


def read_split_folder(task_root: str | Path, folder: str) -> tuple[list[SplitFile], str]:
    """The folder's files as the queue's split files (``<folder>/<name>``, in
    task order) and the overview's text ('' when there is none)."""
    root = Path(task_root)
    name = str(folder or '').strip().strip('/\\')
    if not name or _unsafe(name):
        raise SplitFolderError(f'{folder!r} is not a folder of the task')
    split = _split_folder(root, root / name)
    if split is None:
        raise SplitFolderError(
            f'{name}/ holds no split: it needs at least {MIN_SPLIT_FILES} markdown files '
            'whose names start with a number',
        )
    files = [SplitFile(f'{name}/{file_name}', _read(root / name / file_name)) for file_name in split.files]
    overview = _read(root / name / split.overview) if split.overview else ''
    return files, overview


def _split_folder(root: Path, child: Path) -> SplitFolder | None:
    if child.name.startswith('.') or child.is_symlink() or not child.is_dir():
        return None
    if (child / '.git').exists():
        return None  # a repository clone, never a split
    numbered = []
    overview = ''
    for entry in child.iterdir():
        if not entry.is_file() or entry.is_symlink():
            continue
        if entry.name.lower() == OVERVIEW_NAME:
            overview = entry.name
            continue
        match = _NUMBERED.match(entry.name)
        if match:
            numbered.append((int(match.group(1)), entry.name))
    if len(numbered) < MIN_SPLIT_FILES:
        return None
    return SplitFolder(
        path=child.relative_to(root).as_posix(),
        files=tuple(file_name for _number, file_name in sorted(numbered)),
        overview=overview,
    )


def _unsafe(name: str) -> bool:
    """Only a folder directly in the task folder, named plainly."""
    return '/' in name or '\\' in name or name.startswith('.') or name in ('..', '.')


def _read(path: Path) -> str:
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise SplitFolderError(f'{path.name} is too large to be a task')
        return path.read_text(encoding='utf-8')
    except UnicodeDecodeError as exc:
        raise SplitFolderError(f'{path.name} is not readable text') from exc
    except OSError as exc:
        raise SplitFolderError(f'{path.name} could not be read: {exc.strerror or exc}') from exc
