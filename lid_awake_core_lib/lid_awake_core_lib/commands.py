"""Run one system command from the tick, bounded and with no terminal.

The tick must never hang on a command: a stuck tick stops the heartbeat, and
the watchdog then lets the computer sleep under work that was fine. Nor may a
command read the operator's terminal (``sudo -n`` must fail, never prompt), or
flash a console window on Windows.
"""

from __future__ import annotations

import subprocess

COMMAND_TIMEOUT_SECONDS = 5.0
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def run_command(argv: list[str]) -> subprocess.CompletedProcess | None:
    """The finished command, or ``None`` when it could not run or finish."""
    try:
        return subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
            check=False,
            creationflags=NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return None
