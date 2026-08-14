"""Classify whether an agent tool call reaches outside its sandbox.

A the orchestrator Claude session is spawned with a containment sandbox: the
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

from agent_core_lib.agent_core_lib.helpers.command_introspection import (
    deobfuscate_command,
    split_command_segments,
    split_heredoc_bodies,
)

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

    the orchestrator clones every repo of a task as a SIBLING under one task
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


def effective_sandbox_roots(
    cwd: str, additional_dirs: tuple[str, ...] | list[str] = (),
) -> list[str]:
    """The sandbox roots that count as "inside the task folder": the ``cwd``,
    the spawn-time ``--add-dir`` set, and the task-folder parent (see
    ``_effective_roots``). Public so the PREVENTIVE write-scope settings and
    this POST-HOC classifier share ONE boundary — a write allowed here is
    exactly a write that won't be warned about later. Empty when ``cwd`` is
    unknown."""
    return _effective_roots(cwd, additional_dirs)


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
    agent touch even though they live outside the task folder — e.g. the orchestrator's
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


# Absolute-path substrings ANYWHERE in a command — not just space-separated
# tokens. The path the agent reads is often buried inside quotes / a function
# call, e.g. ``python3 -c "...open('/Users/x/other/file.py')..."`` — a
# token-only scan (looking for args that START with ``/``) misses those.
_COMMAND_ABS_PATH = re.compile(r'[~/][\w./~+=*-]*')
# Relative paths that climb OUT with ``..`` (e.g. ``../../other-repo/secret``),
# anywhere in the command. The lookbehind keeps it from re-matching the tail of
# an absolute path already caught above (``/a/b/../c`` — the ``../c`` there is
# preceded by ``/``). Resolved against ``cwd``; only ones that actually escape
# the sandbox are flagged, so an in-task ``../sibling-repo/x`` stays clean.
_COMMAND_REL_DOTDOT = re.compile(r'(?<![\w/~])(?:[\w.~+=*-]+/)*\.\.(?:/[\w.~+=*-]*)*')
# Only USER / PROJECT space is interesting for ABSOLUTE paths: other repos and
# secrets live under the home tree. System paths (/usr,/etc,…), URLs (//host/…),
# and glob/regex fragments (``/main/*``) fall outside these prefixes and are
# ignored — that is what keeps command scanning from drowning the operator in
# false alarms. (Relative ``..`` escapes are flagged regardless of destination:
# climbing out of the workspace is itself the signal.)
_USER_SPACE_PREFIXES = ('/Users/', '/home/')

# A bare multi-segment relative path with NO leading ``/``/``~``/``..`` —
# e.g. ``OTHER-TASK-999/repoX/secret.txt``. Needed because a ``cd`` chain
# that climbs out (``cd .. && cd ..``) followed by a plain filename is
# otherwise invisible to any scanner: the filename itself carries no
# escape marker, only the ACCUMULATED effect of the prior ``cd`` hops
# reveals it resolves outside the sandbox. Same lookbehind convention as
# ``_COMMAND_REL_DOTDOT`` (not word/``/``/``~``) so a match starts at the
# token's real beginning, not mid-token.
_COMMAND_BARE_RELATIVE = re.compile(r'(?<![\w/~])[\w.+=*-]+(?:/[\w.+=*-]+)+')

# A ``cd <target>`` segment — used to simulate the shell's cumulative
# working directory across a ``&&``/``;``/``|``-chained command so later
# segments' relative paths resolve against where the shell ACTUALLY is by
# then, not the frozen session ``cwd``. ``cd -`` (previous dir, unknown
# without tracking ``OLDPWD``) and a bare ``cd`` (home) are deliberately
# left unhandled — under-tracking here just means a path is checked
# against a stale cwd, the same static-only limitation every other check
# in this module already has.
_CD_COMMAND_RE = re.compile(r'^cd\s+(\S.*)$')


def _next_simulated_cwd(segment: str, current_cwd: str) -> str:
    if not current_cwd:
        return current_cwd
    match = _CD_COMMAND_RE.match(segment.strip())
    if not match:
        return current_cwd
    target = match.group(1).strip()
    if not target or target == '-':
        return current_cwd
    return _normalize(os.path.expanduser(target), current_cwd)


def _absolute_path_args(text: str) -> list[str]:
    """Home-tree ABSOLUTE paths in ``text`` — the cwd-independent subset.

    Split out of ``_segment_path_args`` so heredoc bodies can be scanned with
    only this rule. An absolute path means the same thing wherever it appears,
    so a body needs no cwd simulation to judge one.
    """
    args: list[str] = []
    for match in _COMMAND_ABS_PATH.finditer(text):
        raw = match.group(0)
        if len(raw) < 2:
            continue
        # Normalize before the home-tree test so ``~``, ``..``, ``.`` and
        # doubled slashes can't dodge it (e.g. ``/Users/x/../../etc``).
        if os.path.normpath(os.path.expanduser(raw)).startswith(_USER_SPACE_PREFIXES):
            args.append(raw)
    return args


def _segment_path_args(segment: str) -> list[str]:
    """Filesystem paths referenced anywhere in ONE command segment that are
    worth sandbox-checking: absolute home-tree paths, relative ``..``
    escapes, and bare multi-segment relative paths.

    Quotes/backslashes are stripped by the caller first so a buried or
    split path is seen whole. System paths, URLs, and glob fragments are
    left out of the absolute set on purpose (low-noise); relative paths
    are included and the caller decides (against the simulated cwd) if
    they actually escape."""
    args: list[str] = _absolute_path_args(segment)
    for match in _COMMAND_REL_DOTDOT.finditer(segment):
        raw = match.group(0)
        if '..' in raw.split('/'):  # a real ``..`` segment, not ``foo..bar``
            args.append(raw)
    for match in _COMMAND_BARE_RELATIVE.finditer(segment):
        raw = match.group(0)
        args.append(raw)
    return args


def classify_command_sandbox(
    command: str,
    cwd: str,
    additional_dirs: tuple[str, ...] | list[str] = (),
    allowed_paths: tuple[str, ...] | list[str] = (),
) -> tuple[bool, str]:
    """Return ``(outside, offending_path)`` for a shell command's path args.

    Companion to ``classify_tool_input_sandbox`` for Bash: a ``grep`` / ``cat``
    / ``python -c "open('…')"`` naming a path that escapes the sandbox (another
    repo, ``~/.ssh``, a ``../../`` climb-out) is flagged so the UI can warn +
    withhold a remembered grant. Hardened against quote-splitting, backslash
    escaping and ``$HOME`` indirection; the sandbox roots + ``allowed_paths``
    allow-list are exempt, so ordinary ``git``/``ls``/``mvn`` never trips it.

    Processed segment-by-segment (``&&``/``||``/``;``/``|``), simulating
    the cumulative effect of any ``cd`` segments — a multi-hop escape
    split across separate ``cd ..`` hops (an ordinary, unsuspicious shell
    idiom) is otherwise judged against the session's FROZEN original
    ``cwd`` and never resolves as escaping, even though the shell itself
    has genuinely walked out of the sandbox by the time the final
    segment runs.

    Static-only by nature: a path computed at runtime ($VAR, base64, fetched)
    is invisible here — the docker setting is the OS-level guarantee."""
    norm_roots = _effective_roots(cwd, additional_dirs)
    if not norm_roots:
        return False, ''
    norm_allowed = {_normalize(p, cwd) for p in allowed_paths if p}
    # Heredoc bodies are DATA, not arguments — a file being written, a patch, a
    # SQL script. Scanning them with the same rules as shell text made every
    # relative path MENTIONED in prose ("see ../../docs/setup.md") read as a
    # path being opened, so writing documentation tripped the out-of-scope
    # warning. They are still scanned, but only for ABSOLUTE / home-tree paths:
    # that keeps the case worth catching (``open('/Users/me/.ssh/id_rsa')``
    # smuggled into a heredoc) while dropping the prose false positives.
    #
    # Known residual: a RELATIVE climb-out written inside a heredoc body is no
    # longer flagged here. Accepted deliberately — it is a warning layer, and
    # the docker sandbox is the structural boundary.
    shell_text, heredoc_bodies = split_heredoc_bodies(deobfuscate_command(command))
    current_cwd = os.path.normpath(cwd) if cwd else cwd
    for segment in split_command_segments(shell_text):
        for raw in _segment_path_args(segment):
            resolved = _normalize(os.path.expanduser(raw), current_cwd)
            if resolved in norm_allowed:
                continue
            if not any(_is_within(resolved, root) for root in norm_roots):
                return True, raw
        current_cwd = _next_simulated_cwd(segment, current_cwd)
    for body in heredoc_bodies:
        # cwd-independent by construction: only absolute paths are considered.
        for raw in _absolute_path_args(body):
            resolved = _normalize(os.path.expanduser(raw), current_cwd)
            if resolved in norm_allowed:
                continue
            if not any(_is_within(resolved, root) for root in norm_roots):
                return True, raw
    return False, ''
