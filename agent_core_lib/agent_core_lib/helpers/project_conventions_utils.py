"""A repository's own commit/PR conventions: its ``CLAUDE.md``, else ``AGENTS.md``.

A plain read, no framing — the caller decides how to present it. This is NOT
``agents_instruction_utils.py``: that module only ever reads ``AGENTS.md``,
and only to tell the AGENT to follow it in its own prompt. The host reads
conventions text for a different reason entirely — to ask a separate,
tool-less model call to write the host's own commit message / PR text to
match them — so it needs the raw text, and it needs ``CLAUDE.md`` too: that
is the file a developer actually writes commit/PR rules into, and the one
Claude Code itself already reads natively when a person works by hand.
``CLAUDE.md`` wins over ``AGENTS.md`` in a directory that has both, since it
is the real case this exists for; a team that only keeps the newer,
tool-agnostic file is still covered.
"""

from __future__ import annotations

import os
from pathlib import Path

from utils_core_lib.utils_core_lib.text_utils import normalized_text, text_from_attr

#: Checked in this order per directory; the first one present wins.
CONVENTION_FILE_NAMES = ('CLAUDE.md', 'AGENTS.md')
SKIPPED_DIRECTORIES = frozenset({'.git'})


def repository_conventions_text(repositories: list[object]) -> str:
    """Raw ``CLAUDE.md``/``AGENTS.md`` text from each repository, labeled
    and concatenated. ``''`` when no repository has either file anywhere.
    """
    sections: list[str] = []
    for repository in repositories or []:
        section = _repository_section(repository)
        if section:
            sections.append(section)
    return '\n\n'.join(sections)


def _repository_section(repository: object) -> str:
    local_path = normalized_text(text_from_attr(repository, 'local_path'))
    if not local_path:
        return ''
    root = Path(local_path)
    if not root.is_dir():
        return ''
    entries = _convention_entries(root)
    if not entries:
        return ''
    repository_id = normalized_text(text_from_attr(repository, 'id')) or root.name
    lines = [f'Repository {repository_id}:']
    for relative_path, content in entries:
        lines.append(f'{relative_path}:')
        lines.append(content)
    return '\n'.join(lines)


def _convention_entries(root: Path) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for current_root, dir_names, file_names in os.walk(root):
        dir_names[:] = sorted(
            name for name in dir_names if name not in SKIPPED_DIRECTORIES
        )
        name = next((n for n in CONVENTION_FILE_NAMES if n in file_names), '')
        if not name:
            continue
        path = Path(current_root) / name
        relative_path = path.relative_to(root).as_posix()
        entries.append((relative_path, _read_convention_file(path)))
    return entries


def _read_convention_file(path: Path) -> str:
    return path.read_text(encoding='utf-8', errors='replace').strip()
