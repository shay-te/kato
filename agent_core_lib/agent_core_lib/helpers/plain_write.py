"""Is a file write ordinary work inside the task folder?

"Edit automatically" promises edits without asking, and the CLI cannot keep that
promise here: the catch-all ``ask`` rules that stop a write outside the folder
also beat every ``allow``, so each write arrives as a permission request, inside
the folder too. The host answers those, and this decides which ones it may
answer without a person.

Stricter than ``classify_tool_input_sandbox``, which reports what it can see
escape, by reading the path alone. This approves, so it must see every path
land inside: symlinks are followed (a repository can carry one pointing
anywhere), and no path means no.

Some files in the folder are not project content. They decide what runs next,
with no approval anywhere after the write: a hook in ``.git``, a setting in
``.claude``, a server in ``.mcp.json``. Those keep asking.
"""

from __future__ import annotations

import os
from typing import Any

from agent_core_lib.agent_core_lib.helpers.sandbox_scope import (
    candidate_paths,
    effective_sandbox_roots,
    is_within,
)

WRITE_TOOLS = frozenset({'Write', 'Edit', 'MultiEdit', 'NotebookEdit'})

_CONTROL_NAMES = frozenset({
    '.git', '.claude', '.vscode', '.idea',
    '.gitconfig', '.gitmodules', '.mcp.json', '.claude.json',
})


def is_plain_write(
    tool_name: str,
    tool_input: Any,
    cwd: str,
    additional_dirs: tuple[str, ...] | list[str] = (),
    allowed_paths: tuple[str, ...] | list[str] = (),
    reserved_prefixes: tuple[str, ...] = (),
) -> bool:
    """Whether this write needs no approval beyond the mode the task is on.

    ``allowed_paths`` are the exact files the host hands the agent outside the
    folder. ``reserved_prefixes`` name the host's own files inside it, which
    are as much control files as the ones listed here.
    """
    paths = candidate_paths(tool_input)
    roots = effective_sandbox_roots(cwd, additional_dirs)
    if tool_name not in WRITE_TOOLS or not paths:
        return False
    try:
        allowed = {_real_path(path, cwd) for path in allowed_paths if path}
        real_roots = [_real_path(root, cwd) for root in roots]
        return all(
            _is_plain(_real_path(path, cwd), real_roots, allowed, reserved_prefixes)
            for path in paths
        )
    except (OSError, ValueError):
        return False


def _real_path(path: str, cwd: str) -> str:
    # POSIX refuses a NUL in a path; Windows' realpath passes it through, and
    # the write it names could never land. Refused everywhere, so the answer
    # does not depend on the platform.
    if '\x00' in path:
        raise ValueError('a path cannot hold a NUL character')
    return os.path.normcase(os.path.realpath(os.path.join(cwd, path)))


def _is_plain(
    path: str, roots: list[str], allowed: set[str], reserved_prefixes: tuple[str, ...],
) -> bool:
    if path in allowed:
        return True
    root = next((root for root in roots if is_within(path, root)), None)
    if root is None:
        return False
    names = os.path.relpath(path, root).split(os.sep)
    return not any(_is_control_name(name, reserved_prefixes) for name in names)


def _is_control_name(name: str, reserved_prefixes: tuple[str, ...]) -> bool:
    # Lower-cased, and cut at what Windows ignores: on a disk that ignores
    # case, ``.GIT`` is ``.git``, and there ``.git.`` and ``.git::$DATA`` are too.
    plain = name.lower().split(':', 1)[0].rstrip(' .')
    return plain in _CONTROL_NAMES or plain.startswith(reserved_prefixes)
