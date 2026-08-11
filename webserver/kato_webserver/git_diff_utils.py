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

import re
import subprocess
from pathlib import Path
from typing import Any

from git_core_lib.git_core_lib.helpers.git_command_utils import build_safe_git_command


# Caps for synthesized "new file" diff hunks (untracked working-tree files
# that have no git index entry yet). Anything bigger gets a placeholder
# instead of dumping megabytes into the diff response.
UNTRACKED_FILE_LINE_LIMIT = 1500
UNTRACKED_FILE_BYTE_LIMIT = 256 * 1024

# A real commit SHA (full or abbreviated) is hex digits only. Enforced
# before any caller-supplied "sha" reaches a git argv — git subcommands
# like `show`/`log`/`diff` accept `--output=<file>`-style options in ANY
# positional slot, so an unvalidated value (e.g. from a query param) is
# an arbitrary-file-write primitive, not just a lookup key.
_COMMIT_SHA_RE = re.compile(r'^[0-9a-fA-F]{4,40}$')

# Per-file cap for the main ``git diff`` output. A single changed file
# whose diff section exceeds this many lines has its body replaced
# with a one-line notice. Without this, a changeset that touches large
# minified build artifacts (bundled ``*.chunk.js`` / ``main.<hash>.js``)
# returns a multi-megabyte payload that the browser parses + renders
# all at once — the diff pane freezes on "Computing diff…". The file
# still appears in the tree with its real path; the operator opens it
# in the editor pane to see the full content.
#
# Both a line cap AND a byte cap are needed: minified bundles are
# often a HANDFUL of lines that are each hundreds of KB, so a
# line-count check alone would wave a multi-megabyte single-line diff
# straight through.
TRACKED_FILE_DIFF_LINE_LIMIT = 2000
TRACKED_FILE_DIFF_BYTE_LIMIT = 128 * 1024


def run_git(cwd: str, args: list[str], *, timeout: float) -> str | None:
    """Run ``git -C <cwd> <args>`` and return stdout, or None on any failure.

    Returning ``None`` rather than ``''`` lets callers tell "git failed"
    apart from "git ran and the answer was empty".

    Built through ``build_safe_git_command`` (not a bare
    ``['git', '-C', cwd, *args]``) so every invocation disables git
    hooks (``core.hooksPath=/dev/null``) — a per-task workspace clone
    is agent-writable, including its own ``.git/hooks/``, so any git
    command run here without that flag risks executing a hook the
    agent planted, with the OPERATOR's own privileges (a real
    sandbox-escape path a previous version of this function had).
    """
    if not cwd:
        return None
    try:
        result = subprocess.run(
            build_safe_git_command(cwd, args),
            capture_output=True,
            text=True,
            # Pin UTF-8 so a smart-quote in a commit message or a
            # branch name doesn't blow up the stdout reader thread
            # with ``UnicodeDecodeError`` on Windows (default cp1252).
            encoding='utf-8',
            errors='replace',
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


def _ref_exists(cwd: str, ref: str) -> bool:
    """True when ``ref`` resolves to a commit in this clone."""
    if not cwd or not ref:
        return False
    return run_git(
        cwd, ['rev-parse', '--verify', '--quiet', f'{ref}^{{commit}}'], timeout=5,
    ) is not None


def has_origin_remote(cwd: str) -> bool:
    """True when the clone has an ``origin`` remote configured.

    A per-task workspace clone that kato provisioned always does. A git
    folder the operator copied straight into the task — a repo that was
    never pushed to any host — does not, and that is exactly the case where
    ``origin/<base>`` cannot be the diff base.
    """
    if not cwd:
        return False
    out = run_git(cwd, ['remote'], timeout=5)
    if not out:
        return False
    return any(line.strip() == 'origin' for line in out.splitlines())


def resolve_base_ref(cwd: str, base: str) -> tuple[str, bool]:
    """Pick the best EXISTING ref to diff the task branch against.

    Returns ``(ref, is_local_fallback)``. Preference order:

      1. ``origin/<base>`` — the normal cloud-repo case.
      2. a local ``<base>`` branch — repo present but base only local.
      3. ``HEAD`` (``is_local_fallback=True``) — no reachable base at all.

    The ``HEAD`` fallback is what makes a clone with NO reachable base — a
    git folder the operator copied into the task and never pushed — still
    show its changes: ``git diff HEAD`` surfaces the working-tree edits the
    operator sees in their editor, instead of the empty diff a non-existent
    ``origin/<base>`` produced (the "kato ignores the changes" bug).
    """
    if base:
        for ref in (f'origin/{base}', base):
            if _ref_exists(cwd, ref):
                return ref, False
    return 'HEAD', True


def detect_default_branch(cwd: str) -> str:
    """Repo's default branch as published by the remote, or '' on failure.

    This is a *fallback* used by the diff endpoint when the kato
    config has no ``destination_branch`` for the repo. It is NOT
    the right answer for diffing a kato task branch — kato always
    forks a task off the configured ``destination_branch`` for
    that repo, which may not be the remote's default. Probing
    ``origin/main`` or ``origin/master`` blindly produced wrong
    diffs (the operator saw hundreds of unrelated commits because
    the task base was ``develop``); we used to do that and stopped.

    Resolution order:

    1. ``git symbolic-ref refs/remotes/origin/HEAD`` — works when
       the local clone has its HEAD ref set (the common case).
    2. ``git ls-remote --symref origin HEAD`` — asks the remote
       directly. Works even when step 1 returns nothing because
       the workspace clone never had ``origin/HEAD`` set.

    Empty string means we could not determine the remote default
    — the caller surfaces a precise error so the operator can fix
    the config rather than silently picking a wrong base.
    """
    return _branch_from_local_head(cwd) or _branch_from_ls_remote(cwd)


def _branch_from_local_head(cwd: str) -> str:
    """Read ``refs/remotes/origin/HEAD`` if the clone has it set."""
    out = run_git(
        cwd, ['symbolic-ref', '--short', 'refs/remotes/origin/HEAD'], timeout=5,
    )
    if out is None:
        return ''
    ref = out.strip()
    return ref.split('/', 1)[1] if '/' in ref else ref


def _branch_from_ls_remote(cwd: str) -> str:
    """Ask the remote what HEAD points at, via ``git ls-remote --symref``.

    Output format::

        ref: refs/heads/develop\\tHEAD
        <sha>\\tHEAD

    Independent of the local clone's HEAD ref state — works even
    when the local clone never set ``refs/remotes/origin/HEAD``,
    which is the case kato hit in production with Bitbucket repos
    whose default branch is ``develop``.
    """
    out = run_git(cwd, ['ls-remote', '--symref', 'origin', 'HEAD'], timeout=10)
    if out is None:
        return ''
    for line in out.splitlines():
        if not line.startswith('ref:'):
            continue
        # ``ref: refs/heads/<branch>\tHEAD`` → grab the branch name.
        ref_part = line.split('\t', 1)[0]
        if ':' not in ref_part:  # pragma: no cover - defensive; ``line`` starts with ``ref:`` so the prefix always contains ':'.
            continue
        ref = ref_part.split(':', 1)[1].strip()
        prefix = 'refs/heads/'
        if ref.startswith(prefix):
            return ref[len(prefix):]
        return ref
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


def _diff_base(cwd: str, base_ref: str) -> str:
    """The ref the working tree is diffed against: ``merge-base(base_ref, HEAD)``.

    ``base_ref`` is the current *tip* of the destination branch (e.g.
    ``origin/master``). Diffing the working tree straight against it is
    two-dot semantics, so the moment ``master`` advances past the task
    branch's fork point, every file added to ``master`` *after* the fork
    shows up as a phantom DELETION — the operator saw thousands of
    deleted lines (migrations, services) that weren't in the PR at all.

    The PR uses three-dot ``base...HEAD`` (i.e. the merge-base). Anchoring
    on the merge-base here makes the Changes tab / Files tree agree with
    the PR, while ``git diff <merge-base>`` still spans merge-base →
    working tree so uncommitted work stays visible.

    Falls back to ``base_ref`` when there is no common ancestor (unrelated
    histories) or git cannot resolve the merge-base — no worse than the
    old tip-diff behaviour in that corner case.
    """
    if not cwd or not base_ref:
        return base_ref
    out = run_git(cwd, ['merge-base', base_ref, 'HEAD'], timeout=10)
    return (out or '').strip() or base_ref


def changed_paths(cwd: str, base_ref: str) -> list[str]:
    """Repo-relative paths that differ from ``base_ref``.

    Same coverage as the Changes-tab diff (``diff_against_base``) so
    the Files tree and the Changes tab agree on "what changed":

      * ``git diff --name-only <merge-base>`` — tracked files with
        committed OR uncommitted edits since the branch forked
        (merge-base of ``base_ref`` and HEAD — see ``_diff_base`` for
        why the tip of ``base_ref`` is the wrong anchor);
      * ``git ls-files --others --exclude-standard`` — untracked,
        non-ignored files Claude just wrote (not yet in the index,
        so the diff above misses them).

    Best-effort: an empty list on any git failure (no upstream,
    bad base ref, git not on PATH) — the tree just renders without
    change colouring rather than erroring.
    """
    if not cwd or not base_ref:
        return []
    paths: set[str] = set()
    tracked = run_git(cwd, ['diff', '--name-only', _diff_base(cwd, base_ref)], timeout=20)
    if tracked:
        for line in tracked.splitlines():
            path = line.strip()
            if path:
                paths.add(path)
    untracked = run_git(
        cwd, ['ls-files', '--others', '--exclude-standard'], timeout=15,
    )
    if untracked:
        for line in untracked.splitlines():
            path = line.strip()
            if path:
                paths.add(path)
    return sorted(paths)


def list_branch_commits(
    cwd: str,
    base_ref: str,
    *,
    limit: int = 50,
) -> list[dict]:
    """Recent commits on HEAD ahead of ``base_ref``, newest first.

    Returns one ``{sha, short_sha, subject, author, epoch}`` dict
    per commit. Drives the Files-tab "view changes from commit"
    dropdown — the operator picks a commit and the UI shows only
    that commit's diff. Empty list on any failure (no upstream,
    detached HEAD, malformed log output) — the dropdown just
    renders empty in that case rather than spamming an error.

    ``--no-merges`` because merge commits don't represent kato's
    own work; the operator's mental model is "what did kato
    change", and merges are bookkeeping. ``--max-count`` keeps
    the dropdown scannable even on long-running task branches.
    """
    if not cwd or not base_ref:
        return []
    bounded_limit = max(1, min(int(limit), 200))
    fmt = '%H%x09%h%x09%ct%x09%an%x09%s'
    out = run_git(
        cwd,
        [
            'log',
            f'--max-count={bounded_limit}',
            '--no-merges',
            f'--pretty=format:{fmt}',
            f'{base_ref}..HEAD',
        ],
        timeout=15,
    )
    if not out:
        return []
    commits: list[dict] = []
    for line in out.splitlines():
        parts = line.split('\t', 4)
        if len(parts) < 5:
            continue
        sha, short_sha, epoch_text, author, subject = parts
        try:
            epoch = float(epoch_text)
        except ValueError:
            epoch = 0.0
        commits.append({
            'sha': sha.strip(),
            'short_sha': short_sha.strip(),
            'epoch': epoch,
            'author': author.strip(),
            'subject': subject.strip(),
        })
    return commits


def diff_for_commit(cwd: str, sha: str) -> str:
    """Unified diff for a single commit's changes.

    Equivalent to ``git show --no-color <sha>`` minus the leading
    commit header — we want the file-by-file diff payload only,
    so the existing react-diff-view ``parseDiff`` can render it
    the same way it renders the branch-vs-base diff.

    ``sha`` is caller-supplied (the webserver route reads it straight
    from a query param) and MUST be validated as an actual hex
    commit-ish before reaching git — ``git show`` accepts option-style
    arguments like ``--output=<path>`` in this same positional slot,
    so an unvalidated value is an arbitrary-file-write primitive, not
    just an invalid lookup. Anything that isn't hex digits returns
    ``''`` exactly like any other "commit not found" failure.
    """
    safe_sha = str(sha or '').strip()
    if not cwd or not safe_sha or not _COMMIT_SHA_RE.fullmatch(safe_sha):
        return ''
    return run_git(
        cwd,
        ['show', '--no-color', '--pretty=format:', safe_sha],
        timeout=30,
    ) or ''


def blob_size_at_ref(cwd: str, ref: str, path: str) -> int | None:
    """Size of ``path`` at ``ref``, or None when git cannot read it."""
    safe_path = str(path or '').strip().lstrip('/')
    safe_ref = str(ref or '').strip()
    if not cwd or not safe_ref or not safe_path:
        return None
    out = run_git(cwd, ['cat-file', '-s', f'{safe_ref}:{safe_path}'], timeout=10)
    if out is None:
        return None
    try:
        return int(out.strip())
    except ValueError:
        return None


def file_text_at_ref(cwd: str, ref: str, path: str) -> str | None:
    """Text content of ``path`` at ``ref``, or None when git cannot read it."""
    safe_path = str(path or '').strip().lstrip('/')
    safe_ref = str(ref or '').strip()
    if not cwd or not safe_ref or not safe_path:
        return None
    return run_git(cwd, ['show', f'{safe_ref}:{safe_path}'], timeout=15)


def diff_against_base(cwd: str, base_ref: str, full_paths=()) -> str:
    """Unified diff that surfaces committed AND uncommitted work vs ``base_ref``.

    The Changes tab is the single source of truth the user looks at while
    chatting — they want to see what Claude has done so far, regardless
    of whether it's been committed yet. We union three things:

      * ``git diff <merge-base>`` — working tree (tracked + staged) vs the
        merge-base of ``base_ref`` and HEAD (NOT the destination tip — see
        ``_diff_base``). Catches both committed and uncommitted edits in
        one call, and matches the PR's three-dot diff so master advancing
        past the fork point doesn't surface phantom deletions.
      * Untracked-but-not-ignored files — Claude's freshly-written files
        won't appear in the diff above until they're added to the index,
        so we synthesize one ``new file`` hunk per untracked path.
      * Large untracked files get a placeholder hunk instead of dumping
        megabytes into the response.
    """
    main_diff = run_git(cwd, ['diff', _diff_base(cwd, base_ref)], timeout=30) or ''
    return (
        _elide_oversized_file_diffs(main_diff, full_paths=full_paths)
        + _untracked_files_as_diff(cwd)
    )


def _section_path(section: list[str]) -> str:
    """Repo-relative path from a ``diff --git a/<p> b/<p>`` header ('' if absent)."""
    if not section or not section[0].startswith('diff --git '):
        return ''
    for line in section:
        if line.startswith('+++ b/'):
            return line[len('+++ b/'):].strip()
        if line.startswith('--- a/'):
            return line[len('--- a/'):].strip()
    parts = section[0].split(' b/', 1)
    return parts[1].strip() if len(parts) == 2 else ''


def _elide_oversized_file_diffs(diff_text: str, *, full_paths=()) -> str:
    """Replace any single file's huge diff body with a short notice.

    ``git diff`` is a concatenation of per-file sections, each starting
    with ``diff --git a/… b/…``. A changeset that rewrites large
    minified bundles produces sections tens of thousands of lines long;
    shipping them all freezes the browser diff pane. We keep every
    section's HEADER (the ``diff --git`` / ``index`` / mode / ``---`` /
    ``+++`` lines — so react-diff-view still resolves the path and the
    add/delete/modify kind) and, when the section is over the line cap,
    swap its hunks for one context-line hunk. A context line (leading
    space) parses safely for add, delete and modify alike and renders
    as a single neutral informational row — no false +/- counts.
    """
    if not diff_text:
        return diff_text
    lines = diff_text.split('\n')
    sections: list[list[str]] = []
    for line in lines:
        if line.startswith('diff --git ') or not sections:
            sections.append([])
        sections[-1].append(line)
    rebuilt: list[str] = []
    requested_full = {str(p).strip() for p in (full_paths or ()) if str(p).strip()}
    for section in sections:
        over_lines = len(section) > TRACKED_FILE_DIFF_LINE_LIMIT
        # +1 per line for the '\n' the join re-adds.
        over_bytes = (
            sum(len(ln) + 1 for ln in section) > TRACKED_FILE_DIFF_BYTE_LIMIT
        )
        # The operator asked for THIS file in full (the "Show full diff"
        # affordance). Elision exists to keep the pane responsive, not to
        # withhold the change — without an opt-out the diff was simply
        # unreachable, since the notice's "open it in the editor pane"
        # shows the file's current contents, not what changed.
        oversized = (
            (over_lines or over_bytes)
            and _section_path(section) not in requested_full
        )
        if (
            not oversized
            or not section
            or not section[0].startswith('diff --git ')
        ):
            rebuilt.extend(section)
            continue
        hunk_start = next(
            (i for i, ln in enumerate(section) if ln.startswith('@@ ')),
            None,
        )
        if hunk_start is None:
            # No hunk (rename-only / binary stub) — leave it untouched.
            rebuilt.extend(section)
            continue
        header = section[:hunk_start]
        body_bytes = sum(len(ln) + 1 for ln in section[hunk_start:])
        # Name the limit that ACTUALLY tripped. Reporting KB when the line
        # cap fired sent an operator chasing file size for a 114 KB file
        # that was comfortably under the 128 KB byte cap.
        hunk_lines = len(section) - hunk_start
        reason = (
            f'{len(section)} lines > {TRACKED_FILE_DIFF_LINE_LIMIT} line limit'
            if over_lines
            else f'~{body_bytes // 1024 or 1} KB > '
                 f'{TRACKED_FILE_DIFF_BYTE_LIMIT // 1024} KB limit'
        )
        # NOTE: the ``?full=<path>`` escape hatch exists server-side but has
        # no UI control yet, so the text points at the request rather than
        # promising a button that isn't there.
        notice = (
            f'(diff too large to display: {reason}; '
            f'{hunk_lines} hunk lines elided — '
            f'append ?full={_section_path(section)} to the diff request '
            f'to load it in full)'
        )
        rebuilt.extend(header)
        rebuilt.append('@@ -1 +1 @@')
        rebuilt.append(f' {notice}')
    return '\n'.join(rebuilt)


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
