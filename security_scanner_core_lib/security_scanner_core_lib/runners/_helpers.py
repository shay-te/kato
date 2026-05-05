"""Shared utilities for security-scanner runners.

Just the bits every runner needs: directory exclusions, a workspace
walker that honours them, a path normaliser, and a sentinel
exception runners raise when their underlying tool isn't installed.

Keep this thin — anything bigger belongs in its own runner module.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator


# Directories every runner skips. ``.git/`` because scanning packed
# objects yields garbage findings; the rest because they're cached
# dependency trees the operator didn't write.
EXCLUDE_DIRS: frozenset[str] = frozenset({
    '.git',
    '.hg',
    '.svn',
    'node_modules',
    '.venv',
    'venv',
    '.tox',
    '.mypy_cache',
    '.pytest_cache',
    '.ruff_cache',
    '__pycache__',
    'dist',
    'build',
    '.next',
    '.cache',
    'target',          # rust
    '.idea',
    '.vscode',
})


class RunnerUnavailableError(Exception):
    """Raised when a runner's underlying tool isn't on the host.

    The orchestrator surfaces these as ``runner_errors`` in the
    report (informational warning) rather than security findings.
    A missing scanner ≠ a security issue.
    """


def iter_workspace_files(workspace: Path) -> Iterator[Path]:
    """Yield every file under ``workspace``, skipping ``EXCLUDE_DIRS``.

    Pure walker — no content reading, no extension filtering. Each
    runner applies its own filename / extension logic on top.
    """
    if not workspace.is_dir():
        return
    for child in workspace.iterdir():
        if child.is_dir():
            if child.name in EXCLUDE_DIRS:
                continue
            yield from iter_workspace_files(child)
        elif child.is_file():
            yield child


def workspace_relative(workspace: Path, target: Path) -> str:
    """Workspace-relative path string for ``target``.

    Falls back to the absolute string when ``target`` is outside
    ``workspace`` — shouldn't happen given runners stay inside the
    walker, but keeps the helper safe.
    """
    try:
        return str(target.relative_to(workspace))
    except ValueError:
        return str(target)
