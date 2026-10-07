"""Turn a task's per-repository diffs into the one text a reviewer reads.

A reviewer is only as good as what it is shown, so every round shows the
WHOLE task: every repository, every changed file. A large change does not fit
in one prompt, so files are taken whole, in order, until a character budget is
spent; the rest are listed by name with an instruction to read them from the
repository. A file is never cut in half — half a hunk invites findings about
code that is not actually there.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from review_loop_core_lib.review_loop_core_lib.ports import RepoDiff

DEFAULT_BUDGET_CHARS = 300_000

_FILE_HEADER = re.compile(r'^diff --git a/(.+?) b/(.+)$', re.MULTILINE)


@dataclass(frozen=True)
class RenderedDiff(object):
    text: str
    repos: int
    files: int
    omitted_files: tuple[str, ...]

    @property
    def is_empty(self) -> bool:
        return self.files == 0


def candidate_digest(diffs: list[RepoDiff]) -> str:
    """A fingerprint of exactly the change a review looked at.

    A verdict is about the code the reviewer saw. When the tree has moved on
    since — the operator kept chatting, a comment run fixed something, the
    tests turn touched a file — that verdict says nothing about the new code,
    so a "clean" only counts while this still matches.
    """
    digest = hashlib.sha256()
    for repo in sorted(diffs, key=lambda item: item.repo_id):
        for part in (repo.repo_id, repo.diff, repo.error):
            digest.update(part.encode('utf-8', 'surrogatepass'))
            digest.update(b'\0')
    return digest.hexdigest()


def split_file_diffs(diff: str) -> list[tuple[str, str]]:
    """``[(path, that file's diff text)]`` in the order git printed them."""
    matches = list(_FILE_HEADER.finditer(diff or ''))
    chunks: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(diff)
        chunks.append((match.group(2), diff[match.start():end].rstrip('\n') + '\n'))
    return chunks


def render_review_diff(
    diffs: list[RepoDiff], *, budget_chars: int = DEFAULT_BUDGET_CHARS,
) -> RenderedDiff:
    """Render ``diffs`` under ``budget_chars``; see the module docstring."""
    sections: list[str] = []
    omitted: list[str] = []
    files = 0
    spent = 0
    for repo in diffs:
        chunks = split_file_diffs(repo.diff)
        files += len(chunks)
        header = _repo_header(repo, len(chunks))
        sections.append(header)
        spent += len(header)
        for path, chunk in chunks:
            if spent + len(chunk) > budget_chars:
                omitted.append(f'{repo.repo_id}: {path}')
                continue
            sections.append(chunk)
            spent += len(chunk)
    if omitted:
        sections.append(
            '\n### Not shown above (too large to include) — read these in the '
            'repository yourself before reporting on them:\n'
            + ''.join(f'- {entry}\n' for entry in omitted)
        )
    return RenderedDiff(
        text=''.join(sections),
        repos=len(diffs),
        files=files,
        omitted_files=tuple(omitted),
    )


def _repo_header(repo: RepoDiff, file_count: int) -> str:
    branch = f' ({repo.base} → {repo.head})' if repo.base or repo.head else ''
    lines = [f'\n### Repository `{repo.repo_id}`{branch} — {file_count} changed file(s)\n']
    if repo.cwd:
        lines.append(f'Path: {repo.cwd}\n')
    if repo.error:
        lines.append(f'Could not read this repository\'s diff: {repo.error}\n')
    return ''.join(lines)
