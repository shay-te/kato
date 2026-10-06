"""Run a one-shot CLI that the caller can stop while it is still working.

``subprocess.run`` blocks until the child exits or its timeout fires — there is
no way to say "stop now" from another thread. A one-shot agent run can take
half an hour, and an operator who presses Stop expects it to end in seconds,
not when the review it no longer wants finishes on its own.

``run_cancellable`` keeps ``subprocess.run``'s contract (stdin text in,
``CompletedProcess`` out, ``subprocess.TimeoutExpired`` on timeout) so a caller
can swap it in without touching its timeout handling, and adds one thing: when
``cancel_event`` is set, the whole process tree is killed and
``ProcessCancelled`` is raised.

The child leads its own process group (``start_new_session``) so the kill
takes anything it started with it, and so killing the group can never reach
the caller's own group — see ``process_liveness.kill_process_tree``.
"""

from __future__ import annotations

import subprocess
import threading
import time

from agent_core_lib.agent_core_lib.helpers.process_liveness import (
    IS_WINDOWS,
    kill_process_tree,
)

DEFAULT_POLL_SECONDS = 0.25
# How long to wait for the reader thread once the child has been killed. The
# pipes close with the process, so this is only a guard against a wedged OS.
_REAP_SECONDS = 5.0


class ProcessCancelled(RuntimeError):
    """The caller's cancel event fired before the process finished."""


def run_cancellable(
    argv: list[str],
    *,
    input_text: str,
    cwd: str | None,
    env: dict[str, str] | None,
    timeout_seconds: float | None,
    cancel_event: threading.Event,
    poll_seconds: float = DEFAULT_POLL_SECONDS,
    logger=None,
    label: str = 'agent',
) -> subprocess.CompletedProcess:
    """Run ``argv`` with ``input_text`` on stdin; stoppable via ``cancel_event``.

    Returns the ``CompletedProcess`` (text mode, utf-8, undecodable bytes
    replaced) when the child exits on its own. Raises
    ``subprocess.TimeoutExpired`` past ``timeout_seconds`` and
    ``ProcessCancelled`` once ``cancel_event`` is set — in both cases after
    killing the child's whole process tree.
    """
    proc = subprocess.Popen(argv, **_popen_kwargs(cwd, env))
    outcome: dict[str, object] = {}

    def _communicate() -> None:
        try:
            outcome['output'] = proc.communicate(input_text)
        except Exception as exc:  # surfaced on the caller's thread below
            outcome['error'] = exc

    reader = threading.Thread(
        target=_communicate, name=f'{label}-io', daemon=True,
    )
    reader.start()
    deadline = (
        time.monotonic() + float(timeout_seconds) if timeout_seconds else None
    )
    while True:
        reader.join(poll_seconds)
        if not reader.is_alive():
            break
        if cancel_event.is_set():
            _kill(proc, reader, logger=logger, label=label)
            raise ProcessCancelled(f'{label} was cancelled')
        if deadline is not None and time.monotonic() >= deadline:
            _kill(proc, reader, logger=logger, label=label)
            raise subprocess.TimeoutExpired(argv, timeout_seconds)
    if 'error' in outcome:
        raise outcome['error']  # type: ignore[misc]
    stdout, stderr = outcome['output']  # type: ignore[misc]
    return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)


def _popen_kwargs(cwd: str | None, env: dict[str, str] | None) -> dict:
    kwargs: dict = {
        'stdin': subprocess.PIPE,
        'stdout': subprocess.PIPE,
        'stderr': subprocess.PIPE,
        'text': True,
        'encoding': 'utf-8',
        'errors': 'replace',
        'cwd': cwd,
        'env': env,
    }
    if IS_WINDOWS:
        # ``taskkill /T`` walks the tree on Windows; a new group keeps a
        # console Ctrl+C aimed at the caller from reaching the child.
        kwargs['creationflags'] = getattr(
            subprocess, 'CREATE_NEW_PROCESS_GROUP', 0,
        )
    else:
        kwargs['start_new_session'] = True
    return kwargs


def _kill(proc: subprocess.Popen, reader: threading.Thread, *, logger, label: str) -> None:
    kill_process_tree(proc.pid, logger=logger, label=label)
    reader.join(_REAP_SECONDS)
