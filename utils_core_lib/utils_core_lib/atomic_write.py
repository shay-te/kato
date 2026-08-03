"""Atomic JSON writing — the canonical implementation.

THIS is why a shared utils package earns its keep. Three libraries each grew
their own ``atomic_write_json``, all named the same, all claiming to be
atomic — and they offered three DIFFERENT guarantees:

    guarantee                agent_core_lib  kato_core_lib  workspace_core_lib
    atomic rename            yes             yes            yes
    fsync (survives power loss) no           no             yes
    cleanup tmp on failure   no              yes            yes
    caller-visible failure   bool            bool + raise   raise

None was broken, but a caller picking the nearest import got a weaker
durability guarantee than the name implied, with nothing at the call site to
reveal it. A crash-sensitive store written with the non-fsync variant survives
a process crash but not a power loss or kernel panic.

This version takes the strongest of each:
  * temp file in the SAME directory (so ``os.replace`` stays on one filesystem
    and is therefore atomic on both POSIX and Windows),
  * ``fsync`` before the rename, so the bytes are on the platter first,
  * unique temp name, so concurrent writers cannot clobber each other's temp,
  * guaranteed cleanup, so a failure between write and rename leaves no orphan,
  * both failure contracts — return ``False`` by default, or ``raise_on_error``
    for callers that surface write failures to the operator.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any


def atomic_write_json(
    path: str | os.PathLike[str],
    payload: Any,
    *,
    logger: logging.Logger | None = None,
    label: str = '',
    trailing_newline: bool = False,
    raise_on_error: bool = False,
    fsync: bool = True,
) -> bool:
    """Write ``payload`` to ``path`` as pretty-printed JSON, atomically.

    A reader concurrent with this call sees either the previous contents in
    full or the new contents in full — never a partial file.

    Returns ``True`` on success and ``False`` on a failed write (leaving any
    previous file intact), unless ``raise_on_error`` is set.

    ``fsync`` defaults to on: durability across power loss costs one syscall
    and is what makes "atomic" mean what callers assume. Turn it off only for
    a file that is cheap to regenerate and written very frequently.
    """
    target = Path(path)
    serialized = json.dumps(payload, indent=2, sort_keys=True)
    if trailing_newline:
        serialized += '\n'
    descriptor = -1
    tmp_path = ''
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        # Unique temp name in the target's own directory: same filesystem (so
        # the rename is atomic) and no collision between concurrent writers.
        descriptor, tmp_path = tempfile.mkstemp(
            prefix=f'{target.name}.{os.getpid()}-{threading.get_ident()}-'
                   f'{uuid.uuid4().hex[:8]}.',
            suffix='.tmp',
            dir=str(target.parent),
        )
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            descriptor = -1  # fdopen owns it now; don't double-close.
            handle.write(serialized)
            handle.flush()
            if fsync:
                os.fsync(handle.fileno())
        os.replace(tmp_path, target)
        tmp_path = ''
        return True
    except OSError as exc:
        if logger is not None:
            label_text = f' for {label}' if label else ''
            logger.warning(
                'failed to persist json%s at %s: %s', label_text, target, exc,
            )
        if raise_on_error:
            raise
        return False
    finally:
        if descriptor != -1:
            try:
                os.close(descriptor)
            except OSError:
                pass
        # A failure between writing the temp file and renaming it would
        # otherwise leave a uniquely-named orphan behind forever.
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
