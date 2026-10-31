"""Turn a task's per-repository diffs into the text a reviewer reads.

A reviewer is only as good as what it is shown. A full review is shown the
WHOLE task: every repository, every changed file. A large change does not fit
in one prompt, so files are taken whole, in order, until a character budget is
spent; the rest are listed by name with an instruction to read them from the
repository. A file is never cut in half — half a hunk invites findings about
code that is not actually there.

A re-check is shown only how the change MOVED since the last review
(``render_changes_since``). Re-reading code nobody touched cannot show whether
a fix worked; it only hands a new reader the same code to find something else
in, and over many rounds that is what walks a loop away from its task.
"""

from __future__ import annotations

import difflib
import hashlib
import re
from dataclasses import dataclass, replace

from review_loop_core_lib.review_loop_core_lib.ports import RepoDiff

DEFAULT_BUDGET_CHARS = 300_000

# Unchanged lines kept around each move, so a re-check can place it.
_CONTEXT_LINES = 3

_FILE_HEADER = re.compile(r'^diff --git a/(.+?) b/(.+)$', re.MULTILINE)
_REPO_HEADER = re.compile(r'^### Repository `([^`]+)`', re.MULTILINE)
_NOT_SHOWN_HEADING = '\n### Not shown above'
# What differs between two diffs of identical code: blob ids, and hunk
# positions, which shift whenever the base does.
_UNSTABLE_LINE = re.compile(r'^(?:index [0-9a-f]+\.\.[0-9a-f]+.*|@@ [^@]* @@)', re.MULTILINE)


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


def file_digests(diffs: list[RepoDiff]) -> dict[str, str]:
    """A fingerprint of each changed file, so a later round can tell which moved.

    Every file counts, the ones too large to show included: a fix inside one of
    those still has to be re-checked.
    """
    return {
        _file_key(repo.repo_id, path): _digest(_comparable(chunk))
        for repo in diffs
        for path, chunk in split_file_diffs(repo.diff)
    }


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
    return _render([(repo, split_file_diffs(repo.diff)) for repo in diffs], budget_chars)


def render_changes_since(
    diffs: list[RepoDiff],
    earlier_digests: dict[str, str],
    earlier_text: str,
    *,
    budget_chars: int = DEFAULT_BUDGET_CHARS,
) -> RenderedDiff:
    """Only what moved since the review that recorded ``earlier_digests`` and
    was given ``earlier_text`` (``render_review_diff``'s text). Empty when
    nothing moved."""
    digests = file_digests(diffs)
    earlier_files = _files_in(earlier_text)
    moved_repos = []
    for repo in diffs:
        moved = [
            (path, _moved(earlier_files.get(_file_key(repo.repo_id, path)), chunk))
            for path, chunk in split_file_diffs(repo.diff)
            if digests[_file_key(repo.repo_id, path)] != earlier_digests.get(
                _file_key(repo.repo_id, path),
            )
        ]
        if moved:
            moved_repos.append((repo, moved))
    rendered = _render(moved_repos, budget_chars)
    gone = sorted(set(earlier_digests) - set(digests))
    if not gone:
        return rendered
    listed = ''.join(f'- {entry}\n' for entry in gone)
    return replace(
        rendered,
        text=f'{rendered.text}\n### Taken out of the change since the last review:\n{listed}',
        files=rendered.files + len(gone),
    )


def _render(repos: list[tuple[RepoDiff, list[tuple[str, str]]]], budget_chars: int) -> RenderedDiff:
    """``repos``: each repository with the ``(path, text)`` of what to show."""
    sections: list[str] = []
    omitted: list[str] = []
    files = 0
    spent = 0
    for repo, chunks in repos:
        files += len(chunks)
        header = _repo_header(repo, len(chunks))
        sections.append(header)
        spent += len(header)
        for path, chunk in chunks:
            if spent + len(chunk) > budget_chars:
                omitted.append(_file_key(repo.repo_id, path))
                continue
            sections.append(chunk)
            spent += len(chunk)
    if omitted:
        sections.append(
            f'{_NOT_SHOWN_HEADING} (too large to include) — read these in the '
            'repository yourself before reporting on them:\n'
            + ''.join(f'- {entry}\n' for entry in omitted)
        )
    return RenderedDiff(
        text=''.join(sections),
        repos=len(repos),
        files=files,
        omitted_files=tuple(omitted),
    )


def _files_in(rendered_text: str) -> dict[str, str]:
    """Each file's text back out of ``render_review_diff``'s output."""
    body = rendered_text.split(_NOT_SHOWN_HEADING)[0]
    headers = list(_REPO_HEADER.finditer(body))
    files: dict[str, str] = {}
    for index, header in enumerate(headers):
        end = headers[index + 1].start() if index + 1 < len(headers) else len(body)
        for path, chunk in split_file_diffs(body[header.end():end]):
            files[_file_key(header.group(1), path)] = chunk
    return files


def _moved(before: str | None, after: str) -> str:
    """How one file's diff moved: a diff of the two diffs. A file the last
    review was not shown has nothing to compare with, so it comes as it is."""
    if before is None:
        return after
    lines = difflib.unified_diff(
        _comparable(before).splitlines(), _comparable(after).splitlines(),
        lineterm='', n=_CONTEXT_LINES,
    )
    # Past difflib's own ---/+++ header. Its hunk positions count lines of the
    # diff, not of the file, so they would only mislead.
    moved = ['…' if line.startswith('@@') else line for line in list(lines)[2:]]
    return '\n'.join([after.split('\n', 1)[0], *moved]) + '\n'


def _comparable(chunk: str) -> str:
    return _UNSTABLE_LINE.sub('', chunk)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8', 'surrogatepass')).hexdigest()[:16]


def _file_key(repo_id: str, path: str) -> str:
    return f'{repo_id}: {path}'


def _repo_header(repo: RepoDiff, file_count: int) -> str:
    branch = f' ({repo.base} → {repo.head})' if repo.base or repo.head else ''
    lines = [f'\n### Repository `{repo.repo_id}`{branch} — {file_count} changed file(s)\n']
    if repo.cwd:
        lines.append(f'Path: {repo.cwd}\n')
    if repo.error:
        lines.append(f'Could not read this repository\'s diff: {repo.error}\n')
    return ''.join(lines)
