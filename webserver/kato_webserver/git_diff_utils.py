"""Git helpers used by the planning UI's Files / Changes tabs.

The webserver's right pane needs three things from a repo:

* The current branch name (for the branch-safety lock).
* The tracked + untracked file tree (Files tab).
* A unified diff vs the destination branch that includes uncommitted
  modifications and untracked files (Changes tab) — that's the part
  ``git diff origin/master...HEAD`` alone misses.

Pure functions, no Flask. Each one returns ``''``/``[]`` on git failure
so the UI degrades gracefully (empty pane > stack trace).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


# Caps for synthesized "new file" diff hunks (untracked working-tree files
# that have no git index entry yet). Anything bigger gets a placeholder
# instead of dumping megabytes into the diff response.
UNTRACKED_FILE_LINE_LIMIT = 1500
UNTRACKED_FILE_BYTE_LIMIT = 256 * 1024


def run_git(cwd: str, args: list[str], *, timeout: float) -> str | None:
    """Run ``git -C <cwd> <args>`` and return stdout, or None on any failure.

    Returning ``None`` rather than ``''`` lets callers tell "git failed"
    apart from "git ran and the answer was empty".
    """
    if not cwd:
        return None
    try:
        result = subprocess.run(
            ['git', '-C', cwd, *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def current_branch(cwd: str) -> str:
    """Abbreviated HEAD ref of ``cwd``, or '' on failure."""
    out = run_git(cwd, ['rev-parse', '--abbrev-ref', 'HEAD'], timeout=5)
    return out.strip() if out is not None else ''


def local_branch_exists(cwd: str, branch: str) -> bool:
    """True when a local ref named ``branch`` exists in ``cwd``."""
    if not branch:
        return False
    return run_git(
        cwd, ['rev-parse', '--verify', f'refs/heads/{branch}'], timeout=5,
    ) is not None


def remote_branch_exists(cwd: str, branch: str, remote: str = 'origin') -> bool:
    """True when ``<remote>/<branch>`` exists in ``cwd``."""
    if not branch:
        return False
    return run_git(
        cwd, ['rev-parse', '--verify', f'refs/remotes/{remote}/{branch}'],
        timeout=5,
    ) is not None


def ensure_branch_checked_out(cwd: str, branch: str) -> bool:
    """Best-effort: checkout ``branch`` in ``cwd`` when not already on it.

    A per-task workspace clone is supposed to live on the task branch.
    If it has drifted to ``master`` (e.g. because the previous kato
    session crashed mid-publish), this restores it. Tries the local
    branch first; falls back to ``origin/<branch>`` if no local ref
    exists yet (clone-checkout-fail path). Returns True iff the
    workspace ends up on ``branch`` after the call. Non-destructive:
    if the working tree is dirty and checkout would clobber, git
    refuses and we return False without forcing.
    """
    if not branch:
        return False
    if current_branch(cwd) == branch:
        return True
    if local_branch_exists(cwd, branch):
        if run_git(cwd, ['checkout', branch], timeout=15) is None:
            return False
    elif remote_branch_exists(cwd, branch):
        if run_git(
            cwd, ['checkout', '-b', branch, f'origin/{branch}'], timeout=15,
        ) is None:
            return False
    else:
        return False
    return current_branch(cwd) == branch


def detect_default_branch(cwd: str) -> str:
    """Repo's default branch (e.g. ``main`` / ``master``), or '' on failure.

    Tries ``origin/HEAD`` first, then falls back to common names. Empty
    string means we couldn't tell — caller should refuse to compute a
    diff in that case.
    """
    out = run_git(
        cwd, ['symbolic-ref', '--short', 'refs/remotes/origin/HEAD'], timeout=5,
    )
    if out is not None:
        ref = out.strip()
        return ref.split('/', 1)[1] if '/' in ref else ref
    for candidate in ('main', 'master'):
        if run_git(
            cwd, ['rev-parse', '--verify', f'origin/{candidate}'], timeout=5,
        ) is not None:
            return candidate
    return ''


def tracked_file_tree(cwd: str) -> list[dict[str, Any]]:
    """Tracked + untracked-but-not-ignored files as a nested tree.

    Uses ``git ls-files --cached --others --exclude-standard`` so the tree
    matches what a developer sees in their editor.
    """
    out = run_git(
        cwd,
        ['ls-files', '--cached', '--others', '--exclude-standard'],
        timeout=15,
    )
    if out is None:
        return []
    paths = sorted({line.strip() for line in out.splitlines() if line.strip()})
    return _paths_to_tree(paths)


def conflicted_paths(cwd: str) -> list[str]:
    """Return repo-relative paths of files with unmerged (conflicted) entries.

    ``git ls-files --unmerged`` emits one line per conflicted-stage
    entry — typically three per file (stages 1/2/3). We dedupe by
    path and sort for stable output.

    Empty list when the repo has no conflicts (the common case),
    when the directory isn't a git repo, or when ``git`` isn't on
    PATH. Best-effort: a failure here must not block the diff
    payload from rendering.
    """
    output = run_git(cwd, ['ls-files', '--unmerged'], timeout=10)
    if not output:
        return []
    paths: set[str] = set()
    for line in output.splitlines():
        # Format: ``<mode> <hash> <stage>\t<path>``
        if '\t' not in line:
            continue
        path = line.split('\t', 1)[1].strip()
        if path:
            paths.add(path)
    return sorted(paths)


def diff_against_base(cwd: str, base_ref: str) -> str:
    """Unified diff that surfaces committed AND uncommitted work vs ``base_ref``.

    The Changes tab is the single source of truth the user looks at while
    chatting — they want to see what Claude has done so far, regardless
    of whether it's been committed yet. We union three things:

      * ``git diff <base_ref>`` — working tree (tracked + staged) vs the
        destination tip. Catches both committed and uncommitted edits in
        one call.
      * Untracked-but-not-ignored files — Claude's freshly-written files
        won't appear in the diff above until they're added to the index,
        so we synthesize one ``new file`` hunk per untracked path.
      * Large untracked files get a placeholder hunk instead of dumping
        megabytes into the response.
    """
    main_diff = run_git(cwd, ['diff', base_ref], timeout=30) or ''
    return main_diff + _untracked_files_as_diff(cwd)


# ----- internals -----


def _paths_to_tree(paths: list[str]) -> list[dict[str, Any]]:
    root: dict[str, dict[str, Any]] = {}
    for path in paths:
        parts = path.split('/')
        cursor = root
        for index, part in enumerate(parts):
            is_leaf = index == len(parts) - 1
            entry = cursor.setdefault(
                part,
                {
                    'name': part,
                    'path': '/'.join(parts[: index + 1]),
                    'children': None if is_leaf else {},
                },
            )
            if not is_leaf:
                cursor = entry['children']
    return _materialize_tree(root)


def _materialize_tree(level: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for entry in level.values():
        item = {'name': entry['name'], 'path': entry['path']}
        if entry['children'] is not None:
            item['children'] = _materialize_tree(entry['children'])
        items.append(item)
    items.sort(key=lambda item: ('children' not in item, item['name']))
    return items


def _untracked_files_as_diff(cwd: str) -> str:
    out = run_git(
        cwd,
        ['ls-files', '--others', '--exclude-standard'],
        timeout=15,
    )
    if not out:
        return ''
    chunks: list[str] = []
    for line in out.splitlines():
        path = line.strip()
        if path:
            chunks.append(_synthesize_new_file_hunk(cwd, path))
    return ''.join(chunks)


def _synthesize_new_file_hunk(cwd: str, relative_path: str) -> str:
    full_path = Path(cwd) / relative_path
    header = (
        f'diff --git a/{relative_path} b/{relative_path}\n'
        'new file mode 100644\n'
        '--- /dev/null\n'
        f'+++ b/{relative_path}\n'
    )
    try:
        size = full_path.stat().st_size
    except OSError:
        return header + '@@ -0,0 +1 @@\n+(unreadable)\n'
    if size > UNTRACKED_FILE_BYTE_LIMIT:
        return header + (
            f'@@ -0,0 +1 @@\n'
            f'+(file too large to preview: {size} bytes)\n'
        )
    try:
        text = full_path.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        return header + '@@ -0,0 +1 @@\n+(binary file — open in editor)\n'
    lines = text.splitlines()
    truncated = len(lines) > UNTRACKED_FILE_LINE_LIMIT
    if truncated:
        lines = lines[:UNTRACKED_FILE_LINE_LIMIT]
    body_lines = [f'+{line}' for line in lines]
    if truncated:
        body_lines.append(f'+(... truncated at {UNTRACKED_FILE_LINE_LIMIT} lines)')
    body = '\n'.join(body_lines) + '\n' if body_lines else '+\n'
    hunk_header = f'@@ -0,0 +1,{len(body_lines)} @@\n'
    return header + hunk_header + body
