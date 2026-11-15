"""Where the operator's code is, found instead of asked.

Most people keep their repositories in one of a handful of folders (``~/dev``,
``~/code``, ``~/Projects``, ``~/Documents/GitHub`` …). This looks at every
folder directly in the home folder and one level inside those, counts the git
repositories in each (directly in it, or one level down, as in
``~/dev/acme/api``), and suggests the folder with the most repositories
DIRECTLY in it, then the most in all. Ranked by the total alone, ``~/Desktop``
won over ``~/Desktop/dev`` by adding up every folder on the desktop — copies of
the same repositories included, which kato would then find twice.

Only folder NAMES are read — never a file's contents — within a time budget, so
a huge home folder costs seconds, not minutes; what was found by then is the
answer.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

# Folders that never hold a code checkout worth suggesting, and are big.
SKIPPED_NAMES = frozenset({
    'Library', 'Applications', 'Pictures', 'Music', 'Movies', 'Public',
    'node_modules', 'AppData', 'snap', 'OneDrive',
})
# Entries read per folder: a folder of thousands is not a code folder.
MAX_ENTRIES = 300
MAX_RESULTS = 5
DEADLINE_SECONDS = 3.0
# Suggested (and created on request) when no folder holds a repository yet.
NEW_FOLDER_NAME = 'code'


@dataclass(frozen=True)
class CodeFolder:
    path: str
    repositories: int

    def to_dict(self) -> dict:
        return {'path': self.path, 'repositories': self.repositories}


class _Budget:
    def __init__(self, seconds: float, clock: Callable[[], float]) -> None:
        self._clock = clock
        self._ends = clock() + seconds

    @property
    def spent(self) -> bool:
        return self._clock() >= self._ends


def find_code_folders(
    home: Path,
    *,
    exclude: Iterable[Path] = (),
    seconds: float = DEADLINE_SECONDS,
    clock: Callable[[], float] = time.monotonic,
) -> list[CodeFolder]:
    """The folders under ``home`` that hold repositories, the best first.

    A folder whose repositories are all in one of its subfolders gives way to
    that subfolder (``~/Desktop`` to ``~/Desktop/dev``): the tighter folder is
    the one meant. ``exclude`` (kato's own workspaces) is never suggested.
    """
    budget = _Budget(seconds, clock)
    excluded = [Path(path).expanduser() for path in exclude]
    candidates: list[tuple[Path, int, int]] = []  # (folder, directly in it, in all)
    for depth_one in _children(home):
        if budget.spent:
            break
        for candidate in (depth_one, *_children(depth_one)):
            if budget.spent:
                break
            if _is_repository(candidate) or _inside_any(candidate, excluded):
                continue
            direct, total = _count_repositories(candidate, budget)
            if total:
                candidates.append((candidate, direct, total))
    tight = [
        (path, direct, total) for path, direct, total in candidates
        if not any(_inside(other, path) and other_total == total
                   for other, _, other_total in candidates)
    ]
    tight.sort(key=lambda item: (-item[1], -item[2], -len(item[0].parts), str(item[0])))
    return [CodeFolder(str(path), total) for path, _, total in tight[:MAX_RESULTS]]


def new_folder(home: Path) -> Path:
    """The folder offered when none holds a repository yet."""
    return home / NEW_FOLDER_NAME


def _count_repositories(folder: Path, budget: _Budget) -> tuple[int, int]:
    """``(directly in folder, in all)`` — "in all" adds one level down."""
    direct = nested = 0
    for child in _children(folder):
        if budget.spent:
            break
        if _is_repository(child):
            direct += 1
        else:
            nested += sum(1 for grandchild in _children(child) if _is_repository(grandchild))
    return direct, direct + nested


def _children(folder: Path) -> list[Path]:
    try:
        entries = []
        for entry in folder.iterdir():
            if len(entries) >= MAX_ENTRIES:
                break
            if entry.name.startswith('.') or entry.name in SKIPPED_NAMES:
                continue
            if entry.is_dir():
                entries.append(entry)
        return entries
    except OSError:
        return []


def _is_repository(folder: Path) -> bool:
    try:
        return (folder / '.git').exists()
    except OSError:
        return False


def _inside(path: Path, folder: Path) -> bool:
    return path != folder and folder in path.parents


def _inside_any(path: Path, folders: list[Path]) -> bool:
    return any(path == folder or _inside(path, folder) for folder in folders)
