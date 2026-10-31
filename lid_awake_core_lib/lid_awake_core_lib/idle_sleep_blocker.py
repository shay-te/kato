"""Stop the Mac idling to sleep while work runs: ``caffeinate``, tied to the host.

No root, and no way to get stuck: ``-w`` makes caffeinate watch the host's pid
and exit with it, so even a crash ends the hold. It does nothing about a closed
lid; that is what ``mac_sleep_switch`` is for.
"""

from __future__ import annotations

import os
import subprocess
from typing import Callable

CAFFEINATE = '/usr/bin/caffeinate'
STOP_TIMEOUT_SECONDS = 2.0


def caffeinate_command(owner_pid: int) -> list[str]:
    return [CAFFEINATE, '-i', '-w', str(owner_pid)]


class IdleSleepBlocker(object):
    def __init__(
        self,
        *,
        owner_pid: int | None = None,
        spawn: Callable[..., subprocess.Popen] = subprocess.Popen,
    ) -> None:
        self._owner_pid = os.getpid() if owner_pid is None else owner_pid
        self._spawn = spawn
        self._process: subprocess.Popen | None = None

    @property
    def holding(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def hold(self) -> None:
        if self.holding:
            return
        try:
            self._process = self._spawn(
                caffeinate_command(self._owner_pid),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                # Its own session: a Ctrl+C for the host must not reach it
                # before the host has decided to stop.
                start_new_session=True,
            )
        except OSError:
            self._process = None

    def release(self) -> None:
        process, self._process = self._process, None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(STOP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
