"""Hold a logind inhibitor for as long as one small child process lives.

``systemd-inhibit`` runs a command and blocks what it is told to for as long as
that command runs, so on Linux the crash guard is built in: the command is a
loop that ends when the owner dies, or when its heartbeat goes stale. No root,
no setup. The same class serves the lid hold and the milder idle hold.

Whether the desktop obeys is another matter. logind honours the inhibitor, but
some desktops (KDE's PowerDevil especially) handle the lid themselves. Holding
it can be confirmed; a closed lid staying awake cannot be, from here.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Callable

from lid_awake_core_lib.lid_awake_core_lib.mac_crash_guard import STALE_AFTER_MINUTES

SYSTEMD_INHIBIT = 'systemd-inhibit'
WHAT_LID_CLOSED = 'handle-lid-switch:sleep:idle'
WHAT_IDLE_ONLY = 'sleep:idle'
CHECK_EVERY_SECONDS = 30
STOP_TIMEOUT_SECONDS = 2.0
# A refusal comes back at once; a hold just keeps running.
SETTLE_SECONDS = 0.3

_WATCH_SCRIPT = f"""
while kill -0 "$1" 2>/dev/null; do
  if [ -n "$2" ] && [ -n "$(find "$2" -mmin +{STALE_AFTER_MINUTES} 2>/dev/null)" ]; then
    exit 0
  fi
  sleep "$3"
done
"""


class LinuxInhibitor(object):
    def __init__(
        self,
        *,
        what: str,
        who: str,
        why: str,
        heartbeat: Path | str = '',
        owner_pid: int | None = None,
        binary: str = SYSTEMD_INHIBIT,
        check_seconds: float = CHECK_EVERY_SECONDS,
        settle_seconds: float = SETTLE_SECONDS,
        spawn: Callable[..., subprocess.Popen] = subprocess.Popen,
    ) -> None:
        self._what = what
        self._who = who
        self._why = why
        self._heartbeat = str(heartbeat)
        self._owner_pid = os.getpid() if owner_pid is None else owner_pid
        self._binary = binary
        self._check_seconds = check_seconds
        self._settle_seconds = settle_seconds
        self._spawn = spawn
        self._process: subprocess.Popen | None = None

    def command(self) -> list[str]:
        return [
            self._binary,
            f'--what={self._what}',
            f'--who={self._who}',
            f'--why={self._why}',
            '--mode=block',
            '/bin/sh', '-c', _WATCH_SCRIPT, 'lid-awake',
            str(self._owner_pid), self._heartbeat, str(self._check_seconds),
        ]

    @property
    def holding(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def is_held(self) -> bool:
        return self.holding

    def set_held(self, held: bool) -> str:
        if held:
            return self._start()
        self.release()
        return ''

    def hold(self) -> None:
        if not self.holding:
            self._start()

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

    def _start(self) -> str:
        try:
            self._process = self._spawn(
                self.command(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
        except OSError as exc:
            self._process = None
            return f'{self._binary} could not start: {exc}'
        time.sleep(self._settle_seconds)
        if self.holding:
            return ''
        detail = self._process.communicate()[1].strip()
        self._process = None
        return f'{self._binary} refused: {detail or "no reason given"}'
