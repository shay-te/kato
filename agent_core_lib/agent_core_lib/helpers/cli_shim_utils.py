"""Bypass the Windows npm ``cmd``-shim when spawning a CLI agent.

Every agent CLI distributed through npm installs as ``<name>.cmd`` on Windows
— a ``cmd.exe`` batch shim that forwards to the real program. Spawning the shim
instead of the program it wraps costs two things:

* **cmd.exe caps its command line at ~8K chars.** An ``--append-system-prompt``
  carrying an architecture document overflows it and raises ``[WinError 206]
  The filename or extension is too long``.
* **cmd.exe SILENTLY truncates at the first raw newline.** A multi-line prompt
  value made every later argument vanish — including ``--resume`` /
  ``--session-id`` / ``--add-dir``. The agent then started a fresh, memoryless
  session under a new id on every single spawn: the Windows resume-amnesia bug.

Resolving the shim to the program it points at sidesteps ``cmd.exe`` entirely
and raises the ceiling to the ``CreateProcess`` maximum (~32K chars).

WHY THIS IS SHARED. The Claude and Codex transports each grew their own copy,
and the copies were not equivalent — Codex's handled strictly less:

    shim shape / quirk                    claude      codex
    native ``"...\\bin\\x.exe"`` target      handled     MISSED
    ``%~dp0`` prefix (raw batch)           handled     handled
    ``%dp0%`` prefix (SETLOCAL, newer npm) handled     MISSED
    nested ``a\\b\\c.js`` reference          handled     MISSED

Codex ships a native binary through npm, so the ``.exe`` shape it missed is the
one its own shim actually uses, and modern npm emits the ``%dp0%`` prefix it
also missed. Both gaps land on the same failure: fall back to the shim, and the
operator gets resume-amnesia on Windows with no error to read. One
version-complete copy is the fix.

Every function is gate-free except :func:`resolve_windows_cli_invocation`, so
tests can drive the real logic on a POSIX host without patching ``os.name`` —
patching that globally makes ``pathlib.Path()`` construct ``WindowsPath`` and
crash before reaching the code under test.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

# npm shims reference their target relative to the shim's own directory, via
# either the raw batch parameter expansion (``%~dp0``) or the SETLOCAL variable
# newer shims assign it to (``%dp0%``).
_SHIM_DIR_TOKENS = ('%~dp0', '%dp0%')
_SHIM_SUFFIXES = ('.cmd', '.bat')


def is_windows_host() -> bool:
    """Whether this process runs on Windows.

    An indirection on purpose: tests patch THIS rather than ``os.name``, which
    would also redirect ``pathlib`` to ``WindowsPath`` and break on POSIX.
    """
    return os.name == 'nt'


def resolve_windows_cli_invocation(cmd_path: str) -> list[str] | None:
    """The argv prefix that bypasses ``cmd_path``'s shim, or ``None``.

    ``None`` on a non-Windows host, on a non-shim binary, or whenever the shim
    can't be parsed with confidence — the caller then spawns ``cmd_path``
    as-is, which is correct for short single-line command lines.
    """
    if not is_windows_host():
        return None
    return resolve_cli_shim_invocation(cmd_path)


def resolve_cli_shim_invocation(cmd_path: str) -> list[str] | None:
    """:func:`resolve_windows_cli_invocation` without the host gate.

    Two shim shapes exist, checked in this order:

    * **native binary** — the shim forwards ``%*`` to a quoted
      ``"...\\bin\\agent.exe"``; return ``[agent.exe]``, no Node involved;
    * **JS entry point** — the shim references ``"...cli.js"``; return
      ``[node.exe, cli.js]``.
    """
    path = Path(cmd_path)
    if path.suffix.lower() not in _SHIM_SUFFIXES:
        return None
    shim_text = read_shim_text(path)
    if shim_text is None:
        return None
    # Native-binary shape first: when present it needs no Node at all, so a
    # host without Node on PATH still gets the bypass.
    exe_match = re.search(r'"([^"]+\.exe)"', shim_text, re.IGNORECASE)
    if exe_match:
        exe_path = resolve_shim_reference(exe_match.group(1), path.parent)
        if exe_path is not None:
            return [str(exe_path)]
    js_match = re.search(r'"([^"]+\.js)"', shim_text)
    if not js_match:
        return None
    js_path = resolve_shim_reference(js_match.group(1), path.parent)
    if js_path is None:
        return None
    node_path = resolve_node_binary(path.parent)
    if node_path is None:
        return None
    return [str(node_path), str(js_path)]


def read_shim_text(path: Path) -> str | None:
    """The shim's text, or ``None`` when it can't be read."""
    try:
        return path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return None


def resolve_shim_reference(ref: str, shim_dir: Path) -> Path | None:
    """Resolve a quoted shim target against the shim's own directory.

    Returns ``None`` when the resolved target doesn't exist — the caller must
    fall back rather than spawn a path that isn't there.
    """
    for token in _SHIM_DIR_TOKENS:
        ref = ref.replace(token + '\\', '').replace(token + '/', '').replace(token, '')
    # Shims use Windows backslash separators. Normalizing them to '/' lets the
    # target resolve regardless of which host parses the shim (real Windows
    # pathlib accepts '/' too, so this is a no-op there). Without it a nested
    # '...\\bin\\agent.exe' reference is read as ONE literal filename and never
    # matches the file — which is exactly how a copy of this code silently
    # stopped bypassing the shim.
    ref = ref.replace('\\', '/')
    target = (shim_dir / ref).resolve()
    if not target.is_file():
        return None
    return target


def resolve_node_binary(shim_dir: Path) -> Path | None:
    """The Node to run a JS entry point with, or ``None`` if there is none.

    Prefers the ``node.exe`` sitting next to the shim (the npm / nvm layout) so
    the agent runs under the same Node version the shim would have used.
    """
    local = shim_dir / 'node.exe'
    if local.is_file():
        return local
    on_path = shutil.which('node')
    if not on_path:
        return None
    return Path(on_path)
