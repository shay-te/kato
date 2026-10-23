"""The release-notes markdown document: where an entry goes, and how it is found again.

Pure text in, text out — no files, no clock — so every rule is tested alone.

The layout is the familiar changelog one::

    # Release notes

    ## Unreleased

    ### ob-love-admin-backend

    - 2026-10-21 — Clients can now set a password (UNA-3272) · [PR](…) <!-- kato:task UNA-3272 -->

    ## 1.4.0
    ...

New entries go under ``## Unreleased``, grouped by repository (a task that
touched several repositories is listed under each). The operator renames
``Unreleased`` to a version when they release; kato never invents one.

Every entry carries an invisible marker, ``<!-- kato:task <ID> -->``. It is the
idempotency key: a task already marked ANYWHERE in the document — including an
entry the operator has since moved into a version section — is never added
again.

Entry text comes from a model and from ticket titles, both untrusted, and the
document is parsed again on the next write — so :func:`clean_line` makes sure
no entry can forge a heading, a marker, or a second line.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

DEFAULT_TITLE = '# Release notes'
UNRELEASED_HEADING = '## Unreleased'
NO_REPOSITORY_HEADING = 'Other'
MAX_TEXT_CHARS = 200

_MARKER = '<!-- kato:task {task_id} -->'
_MARKER_PATTERN = re.compile(r'<!--\s*kato:task\s+(\S+?)\s*-->', re.IGNORECASE)
_UNRELEASED_PATTERN = re.compile(r'^##\s+\[?unreleased\]?\s*$', re.IGNORECASE)
_FENCE_PATTERN = re.compile(r'^\s*(```|~~~)')


def entry_marker(task_id: str) -> str:
    return _MARKER.format(task_id=str(task_id).strip())


def has_entry(markdown: str, task_id: str) -> bool:
    """Whether the document already holds an entry for ``task_id``, anywhere."""
    wanted = str(task_id or '').strip().lower()
    return bool(wanted) and any(
        match.group(1).lower() == wanted for match in _MARKER_PATTERN.finditer(markdown or '')
    )


def clean_line(text: object) -> str:
    """One safe line of entry text from untrusted input.

    The first non-empty line only; no comment delimiters (so no forged
    marker), no leading heading / quote / list characters (so no forged
    heading or nested item), whitespace collapsed, and capped in length.
    """
    lines = [line.strip() for line in str(text or '').splitlines() if line.strip()]
    if not lines:
        return ''
    line = lines[0].replace('<!--', '').replace('-->', '')
    line = re.sub(r'^[\s#>*\-+]+', '', line)
    line = re.sub(r'\s+', ' ', line).strip()
    if len(line) > MAX_TEXT_CHARS:
        line = line[:MAX_TEXT_CHARS - 1].rstrip() + '…'
    return line


def format_entry(
    *, task_id: str, text: str, done_on: str, links: Sequence[tuple[str, str]] = (),
) -> str:
    """``- <date> — <text> (<ID>) · [label](url) <!-- kato:task <ID> -->``.

    The date leads and comes from kato, never from the model: ``text`` is
    cleaned here, so a date (or anything else) inside it cannot take its place.
    """
    task = str(task_id).strip()
    body = clean_line(text) or task
    parts = [f'- {done_on} — {body} ({task})']
    link_text = ' · '.join(
        f'[{clean_line(label) or "link"}]({url})' for label, url in links if _safe_url(url)
    )
    if link_text:
        parts.append(f' · {link_text}')
    parts.append(f' {entry_marker(task)}')
    return ''.join(parts)


def add_entry(markdown: str, *, task_id: str, repositories: Sequence[str], line: str) -> str:
    """``markdown`` with ``line`` under ``## Unreleased`` → ``### <repo>`` for each repository.

    Unchanged when the task already has an entry. ``## Unreleased`` is created
    before the first version section (after any title and introduction), and
    a repository heading at the end of the Unreleased section. The newest entry
    goes first under its heading. Nothing outside the Unreleased section moves.
    """
    if has_entry(markdown, task_id):
        return markdown
    lines = (markdown or '').splitlines()
    if not any(line_.strip() for line_ in lines):
        lines = [DEFAULT_TITLE, '']
    start = _ensure_unreleased(lines)
    for repository in _headings_for(repositories):
        heading = _ensure_repository_heading(lines, start, repository)
        at = heading + 1
        if at < len(lines) and not lines[at].strip():
            at += 1
        lines.insert(at, line)
    _space_headings(lines, start, _section_end(lines, start))
    return '\n'.join(lines).rstrip('\n') + '\n'


def replace_entry_line(markdown: str, *, task_id: str, old: str, new: str) -> tuple[str, int]:
    """Swap the task's entry ``old`` for ``new``, wherever it appears unchanged.

    A line the operator edited no longer equals ``old`` and is left alone —
    the operator's words always win over a later automatic rewrite. Returns
    the document and how many lines were replaced.
    """
    marker = entry_marker(task_id).lower()
    replaced = 0
    lines = (markdown or '').splitlines()
    for index, line in enumerate(lines):
        if line.rstrip() == old.rstrip() and marker in line.lower():
            lines[index] = new
            replaced += 1
    if not replaced:
        return markdown, 0
    return '\n'.join(lines).rstrip('\n') + '\n', replaced


# ----- structure -----

def _headings_for(repositories: Sequence[str]) -> list[str]:
    names: list[str] = []
    for repository in repositories or ():
        name = clean_line(repository)
        if name and name.lower() not in {existing.lower() for existing in names}:
            names.append(name)
    return names or [NO_REPOSITORY_HEADING]


def _outside_fences(lines: list[str]) -> list[bool]:
    """``True`` for each line that is NOT inside a fenced code block."""
    flags, fenced = [], False
    for line in lines:
        if _FENCE_PATTERN.match(line):
            flags.append(False)
            fenced = not fenced
            continue
        flags.append(not fenced)
    return flags


def _is_level(line: str, level: int) -> bool:
    return re.match(rf'^#{{{level}}}\s+\S', line) is not None


def _ensure_unreleased(lines: list[str]) -> int:
    """Index of the ``## Unreleased`` heading, inserting it when missing."""
    usable = _outside_fences(lines)
    for index, line in enumerate(lines):
        if usable[index] and _UNRELEASED_PATTERN.match(line.strip()):
            return index
    first_version = next(
        (index for index, line in enumerate(lines) if usable[index] and _is_level(line, 2)),
        None,
    )
    if first_version is None:
        while lines and not lines[-1].strip():
            lines.pop()
        lines.extend(['', UNRELEASED_HEADING, ''])
        return len(lines) - 2
    lines[first_version:first_version] = [UNRELEASED_HEADING, '']
    return first_version


def _section_end(lines: list[str], start: int) -> int:
    """Index just past the section that starts at ``start`` (its next ``#``/``##``)."""
    usable = _outside_fences(lines)
    for index in range(start + 1, len(lines)):
        if usable[index] and (_is_level(lines[index], 1) or _is_level(lines[index], 2)):
            return index
    return len(lines)


def _ensure_repository_heading(lines: list[str], start: int, repository: str) -> int:
    """Index of ``### <repository>`` inside the Unreleased section, adding it when missing."""
    end = _section_end(lines, start)
    usable = _outside_fences(lines)
    wanted = repository.lower()
    for index in range(start + 1, end):
        line = lines[index]
        if usable[index] and _is_level(line, 3) and line[3:].strip().lower() == wanted:
            return index
    at = end
    while at > start + 1 and not lines[at - 1].strip():
        at -= 1
    lines[at:at] = ['', f'### {repository}', '']
    return at + 1


def _space_headings(lines: list[str], start: int, end: int) -> None:
    """One blank line around each heading of the section, and none piled up."""
    index = start
    while index < min(end, len(lines)):
        if lines[index].startswith('#'):
            if index + 1 < len(lines) and lines[index + 1].strip():
                lines.insert(index + 1, '')
                end += 1
            if index > 0 and lines[index - 1].strip():
                lines.insert(index, '')
                end += 1
                index += 1
        if index + 1 < len(lines) and not lines[index].strip() and not lines[index + 1].strip():
            del lines[index + 1]
            end -= 1
            continue
        index += 1
    # The section ends where the next one starts: keep one blank line before it.
    if end < len(lines) and end > 0 and lines[end - 1].strip():
        lines.insert(end, '')


def _safe_url(url: str) -> bool:
    text = str(url or '').strip()
    return text.startswith(('https://', 'http://')) and not any(c in text for c in ' ()<>\n')
