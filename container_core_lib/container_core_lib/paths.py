"""Is a path Docker reports inside a folder on this computer?

Docker names a bind mount's source, and Compose its project folder, as the
host's Docker saw them — and Docker Desktop rewrites Windows paths on the way:
``C:\\Users\\a`` can come back as ``/run/desktop/mnt/host/c/Users/a``,
``/host_mnt/c/Users/a`` or, from WSL, ``/mnt/c/Users/a``. On a Mac a mount may
read ``/host_mnt/Users/a``. Each form is brought to one before comparing, and a
Windows path compares without case, as Windows does.
"""

from __future__ import annotations

import re

_DRIVE_PREFIXES = ('/run/desktop/mnt/host/', '/host_mnt/', '/mnt/')
_DRIVE_FOLDER = re.compile(r'^([a-zA-Z])(?:/|$)')
_DRIVE_PATH = re.compile(r'^[a-zA-Z]:(?:/|$)')


def host_path(text: object) -> str:
    """``text`` as one comparable form ('' for nothing)."""
    path = str(text or '').strip().replace('\\', '/')
    if not path:
        return ''
    for prefix in _DRIVE_PREFIXES:
        if path.startswith(prefix):
            rest = path[len(prefix):]
            drive = _DRIVE_FOLDER.match(rest)
            if drive:
                path = f'{drive.group(1)}:/{rest[2:]}'
            elif prefix == '/host_mnt/':
                path = f'/{rest}'
            break
    path = re.sub('/+', '/', path)
    if _DRIVE_PATH.match(path):
        path = path.lower()
    return path.rstrip('/') or '/'


def is_inside(path: object, folder: object) -> bool:
    """Whether ``path`` is ``folder`` or anything under it."""
    inner, outer = host_path(path), host_path(folder)
    if not inner or not outer:
        return False
    return inner == outer or inner.startswith(outer.rstrip('/') + '/')
