"""Run a child process that owns Ctrl+C.

A launcher that starts a long-running program and waits for it must not act
on Ctrl+C itself. The terminal delivers SIGINT to the whole foreground
process group — the launcher AND the program — and the program has its own
shutdown to run. ``subprocess.call`` / ``subprocess.run`` do act on it: on
KeyboardInterrupt they wait 0.25s for the child and then SIGKILL it.

With a launcher in front of a launcher in front of the program, that produced
two failures, decided by which wrapper's 0.25s ran out first:

* the program was SIGKILLed half-way through its shutdown, so whatever it was
  stopping (sessions, watchers, subprocesses) was never stopped; or
* the outer launcher killed the inner one first, the prompt came back, and
  the program carried on running detached from the terminal — where a second
  Ctrl+C can no longer reach it, because it now goes to the shell.

Measured on a three-level chain built from the same calls: 7 of 12 Ctrl+Cs
killed the program mid-shutdown, the other 5 left it running behind the
prompt.

Here the launcher ignores SIGINT for exactly as long as the child runs and
waits for it, so the program alone decides how Ctrl+C ends — including a
second Ctrl+C to force it — and the prompt comes back when it has really
gone, with its exit code.
"""

from __future__ import annotations

import signal
import subprocess
from typing import Sequence


def run_leaving_ctrl_c_to_child(command: Sequence[str], **popen_kwargs) -> int:
    """Run ``command`` to completion and return its exit code.

    SIGINT is ignored in THIS process only while the child runs, and only
    after it has been started: a child inherits an ignored SIGINT across
    ``exec``, which would leave it unable to be interrupted at all. The
    previous handler is restored afterwards, whatever happened.
    """
    child = subprocess.Popen(list(command), **popen_kwargs)
    previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        return child.wait()
    finally:
        signal.signal(signal.SIGINT, previous)
