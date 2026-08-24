"""Is that pid still running, and how do I kill it — on every platform.

An agent CLI records its pid so the orchestrator can tell a live session from
a dead one. Answering that reliably is entirely about the host OS, not about
which CLI wrote the pid, and each answer here exists because the obvious
version is wrong somewhere:

* **Probing** must not use ``os.kill(pid, 0)`` on Windows — there that calls
  ``TerminateProcess`` with exit code 0, i.e. it KILLS the process you meant
  only to look at. Windows queries the exit code instead.
* **Killing** must take the tree on Windows: ``TerminateProcess`` (all that
  ``Popen.kill`` can do) ends exactly one process, and an npm ``.cmd`` shim
  makes the real CLI a *child* of the handle the caller holds — killing the
  wrapper orphans it. ``taskkill /T`` walks the children. POSIX has no wrapper
  problem (the shim is a shebang script, so the spawned process IS the CLI),
  where a plain ``SIGKILL`` suffices.
* **Image names** exist to gate against pid recycling: a pid from a registry
  file may since have been reused by something unrelated, and killing that
  would be far worse than leaving a stale entry. Unknown reads as ``''`` and
  the caller decides.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

IS_WINDOWS = os.name == 'nt'


def pid_alive(pid: int) -> bool:
    """Is ``pid`` a currently-running process?"""
    if IS_WINDOWS:
        return _pid_alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Someone else's process: alive, just not ours to signal.
        return True
    except OSError:
        return False
    return True


def kill_process_tree(pid: int, *, logger=None, label: str = 'agent') -> bool:
    """Force-kill ``pid`` AND its children. True when the kill landed.

    ``label`` names the process in the one log line this can emit, so an
    operator reading it knows which CLI failed to die.
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if IS_WINDOWS:
        try:
            completed = subprocess.run(
                ['taskkill', '/T', '/F', '/PID', str(pid)],
                capture_output=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            if logger is not None:
                logger.exception(
                    'taskkill /T /F /PID %s failed to run for %s', pid, label,
                )
            return False
        # 128 = "process not found" — already dead counts as success.
        return completed.returncode in (0, 128)
    import signal

    # ``SIGKILL`` does not exist on Windows; the getattr keeps this branch
    # importable there (tests patch ``IS_WINDOWS`` to exercise both paths on
    # one platform).
    sigkill = getattr(signal, 'SIGKILL', signal.SIGTERM)
    try:
        os.kill(pid, sigkill)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return True


def image_name(pid: int) -> str:
    """Executable image name for ``pid`` (``''`` when unknown).

    Windows asks tasklist; Linux reads ``/proc/<pid>/comm``; anywhere else
    returns ``''``. The caller treats unknown as killable — on POSIX a
    registry pid was written by the CLI itself moments ago, and pid recycling
    inside a session's lifetime is not the realistic hazard it is on Windows.
    """
    if IS_WINDOWS:
        try:
            completed = subprocess.run(
                ['tasklist', '/FI', f'PID eq {int(pid)}', '/FO', 'CSV', '/NH'],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired, ValueError):
            return ''
        rows = (completed.stdout or '').strip().splitlines()
        if not rows:
            return ''
        first_field = rows[0].split('","')[0].strip('"')
        # "INFO: No tasks are running..." lands here when the pid is gone; a
        # real row's first CSV field is the image name.
        if not first_field.lower().endswith('.exe'):
            return ''
        return first_field
    try:
        comm = Path(f'/proc/{int(pid)}/comm').read_text(encoding='utf-8')
    except (OSError, ValueError, UnicodeDecodeError):
        return ''
    return comm.strip()


def coerce_pid(value) -> int | None:
    """``value`` as a positive pid, or ``None`` — registry files are untrusted."""
    try:
        pid = int(value)
    except (TypeError, ValueError):
        return None
    return pid if pid > 0 else None


def _pid_alive_windows(pid: int) -> bool:
    import ctypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid),
    )
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)
