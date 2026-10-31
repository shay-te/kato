"""The host's proof that it is alive, for a watchdog that outlives it.

The file exists only while the host holds the Mac awake, and the host rewrites
it every tick. A file gone stale means the host crashed or hung, and the
watchdog restores normal sleep. No file means the host holds nothing, so the
watchdog leaves the switch alone: an operator's own ``pmset`` is not the host's
to undo.

A timestamp rather than a pid: a hung process still has a pid, and a dead one's
pid gets reused. Only a host that is really running can keep this file fresh.
"""

from __future__ import annotations

from pathlib import Path


class Heartbeat(object):
    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def beat(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.touch()

    def exists(self) -> bool:
        return self._path.exists()

    def clear(self) -> None:
        self._path.unlink(missing_ok=True)
