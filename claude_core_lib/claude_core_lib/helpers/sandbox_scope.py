"""Classify whether an agent tool call reaches outside its sandbox.

A kato Claude session is spawned with a containment sandbox: the
per-task workspace clone (the ``cwd``) plus an explicit ``--add-dir``
set. When the agent asks permission to touch a filesystem path that
escapes ALL of those roots, the planning UI must (a) shout a warning
and (b) refuse to offer a *remembered* approval — an "allow always"
for an out-of-sandbox path would silently hand the agent standing
access outside the task folder on every future run. This module is the
single, pure place that decides "is this path inside the sandbox?" so
the streaming layer can annotate the permission event and every UI
surface agrees.

Pure + filesystem-free: paths are normalized lexically (``normpath``),
never ``realpath``'d, so the classification is deterministic, needs no
disk, and still catches ``../`` escapes (the actual attack vector).
"""

from __future__ import annotations

import os
import re
from typing import Any

# Tool-input keys that name a filesystem path the agent intends to
# touch. Covers Read/Edit/Write/MultiEdit (``file_path``), generic
# ``path``, and NotebookEdit (``notebook_path``). A bare Bash
# ``command`` is intentionally NOT inspected: a shell string can embed
# arbitrarily many paths and parsing it reliably is a fool's errand, so
# flagging it would either miss escapes (false safety) or nuke the
# usefulness of a remembered ``git``/``ls`` approval (false alarm).
# Path-argument tools are the ones where a remembered out-of-sandbox
# grant is actually dangerous, and they are classified exactly.
_PATH_KEYS = ('file_path', 'notebook_path', 'path', 'file')


def _candidate_paths(tool_input: Any) -> list[str]:
    if not isinstance(tool_input, dict):
        return []
    paths: list[str] = []
    for key in _PATH_KEYS:
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            paths.append(value.strip())
    return paths


def _normalize(path: str, base: str) -> str:
    """Lexically resolve ``path`` (relative → against ``base``)."""
    if not os.path.isabs(path):
        path = os.path.join(base or '', path)
    return os.path.normpath(path)


def _is_within(path: str, root: str) -> bool:
    if not root:
        return False
    root_norm = os.path.normpath(root)
    return path == root_norm or path.startswith(root_norm + os.sep)


def _effective_roots(cwd: str, additional_dirs) -> list[str]:
    """Sandbox roots, widened to the whole task folder.

    kato clones every repo of a task as a SIBLING under one task
    workspace dir (``<workspaces>/<task_id>/<repo>``); the live session's
    ``cwd`` is one such repo. The "task folder" the operator means is its
    PARENT. We add that parent as a root so a file in ANY repo of the
    task reads as inside — even a sibling repo that never made it into
    the session's ``--add-dir`` set, which happens for real:
      * the sandbox is immutable post-spawn, so a repo cloned/synced
        after the session started is not in it; and
      * a comment-/review-driven respawn spawns with ``cwd`` only and NO
        ``--add-dir``s at all, leaving a single-root session.
    Both produced the UNA-2727 "inside-the-task file flagged as outside"
    false positive.

    Guards against ballooning the sandbox to unrelated trees:
      * the parent must be at least two levels deep (``/a/b``) — never
        '/' or '/Users' — so it can't swallow every other task's folder
        or the whole disk; and
      * a DIFFERENT task's folder (``<workspaces>/<other-task>``) is
        still outside, because the parent we add is THIS task's folder.
    """
    roots = [os.path.normpath(r) for r in (cwd, *tuple(additional_dirs)) if r]
    cwd_norm = os.path.normpath(cwd) if cwd else ''
    if cwd_norm and os.path.isabs(cwd_norm):
        task_folder = os.path.dirname(cwd_norm)
        deep_enough = task_folder.strip(os.sep).count(os.sep) >= 1
        if deep_enough and task_folder not in roots:
            roots.append(task_folder)
    return roots


def classify_tool_input_sandbox(
    tool_input: Any,
    cwd: str,
    additional_dirs: tuple[str, ...] | list[str] = (),
    allowed_paths: tuple[str, ...] | list[str] = (),
) -> tuple[bool, str]:
    """Return ``(outside, offending_path)`` for an agent tool input.

    ``outside`` is ``True`` when a filesystem-path argument resolves
    OUTSIDE every sandbox root — the task ``cwd`` plus the spawn-time
    ``--add-dir`` set. Relative paths resolve against ``cwd``. The first
    escaping path found is returned (in ``_PATH_KEYS`` order) so the UI
    can name it.

    ``allowed_paths`` are SPECIFIC files the product intentionally lets the
    agent touch even though they live outside the task folder — e.g. kato's
    configured ``lessons_path`` / ``architecture_doc_path``. The agent is
    SUPPOSED to read/write those, so an exact match is never flagged. They
    are passed in (not hard-coded) to keep this lib product-agnostic.

    Conservative on BOTH ends so it neither over-warns nor under-warns:
    - No path argument (e.g. a bare Bash command) → ``(False, '')``. The
      function only flags a path it can actually see escape, preserving
      remembered Bash/git approvals.
    - No sandbox roots known (unconfigured) → ``(False, '')`` rather
      than flag everything.
    """
    norm_roots = _effective_roots(cwd, additional_dirs)
    if not norm_roots:
        return False, ''
    norm_allowed = {_normalize(p, cwd) for p in allowed_paths if p}
    for raw_path in _candidate_paths(tool_input):
        resolved = _normalize(raw_path, cwd)
        if resolved in norm_allowed:
            continue
        if not any(_is_within(resolved, root) for root in norm_roots):
            return True, raw_path
    return False, ''


# Command-segment separators and the system/scratch trees a shell command may
# touch WITHOUT it meaning the agent is rummaging in another project. Reading a
# binary or a config under these is low-signal; flagging them is exactly the
# false-alarm noise that got command-string scanning removed once before. So we
# warn only on escapes into USER / PROJECT space (other repos, ~/.ssh, …).
_COMMAND_SEGMENT_SPLIT = re.compile(r'&&|\|\||[;|]')
_ENV_ASSIGNMENT = re.compile(r'^[A-Za-z_]\w*=')
_SYSTEM_PREFIXES = (
    '/usr', '/bin', '/sbin', '/opt', '/etc', '/var', '/tmp', '/dev',
    '/proc', '/sys', '/Library', '/System', '/private', '/Applications',
)


def _command_path_args(command: str) -> list[str]:
    """Absolute-looking (``/...`` or ``~...``) path ARGUMENTS in a shell
    command — never the program itself.

    Deliberately narrow: per segment we drop leading ``VAR=val`` assignments
    and the program token (the binary, e.g. ``/usr/local/bin/docker``), then
    keep only absolute tokens. Relative args (resolved inside ``cwd``) and
    globs are ignored — parsing every shell token reliably is a fool's errand,
    so we only look at the unambiguous absolute escapes."""
    args: list[str] = []
    for segment in _COMMAND_SEGMENT_SPLIT.split(str(command or '')):
        tokens = [t for t in segment.strip().split() if t]
        i = 0
        while i < len(tokens) and _ENV_ASSIGNMENT.match(tokens[i]):
            i += 1
        i += 1  # skip the program token
        for token in tokens[i:]:
            cleaned = token.strip().strip('\'"').rstrip('\\;),')
            if cleaned and cleaned.startswith(('/', '~')):
                args.append(cleaned)
    return args


def classify_command_sandbox(
    command: str,
    cwd: str,
    additional_dirs: tuple[str, ...] | list[str] = (),
    allowed_paths: tuple[str, ...] | list[str] = (),
) -> tuple[bool, str]:
    """Return ``(outside, offending_path)`` for a shell command's path args.

    Companion to ``classify_tool_input_sandbox`` for Bash: a ``grep`` / ``cat``
    / redirect that names an ABSOLUTE path escaping the sandbox (another repo,
    ``~/.ssh``, …) is flagged so the UI can warn + withhold a remembered
    grant. Conservative by design — the program token, system/scratch dirs,
    relative paths, and the ``allowed_paths`` allow-list are all exempt, so an
    ordinary ``git``/``ls``/``mvn`` never trips it."""
    norm_roots = _effective_roots(cwd, additional_dirs)
    if not norm_roots:
        return False, ''
    norm_allowed = {_normalize(p, cwd) for p in allowed_paths if p}
    for raw in _command_path_args(command):
        resolved = os.path.normpath(os.path.expanduser(raw))
        if resolved in norm_allowed:
            continue
        if any(_is_within(resolved, prefix) for prefix in _SYSTEM_PREFIXES):
            continue
        if not any(_is_within(resolved, root) for root in norm_roots):
            return True, raw
    return False, ''
