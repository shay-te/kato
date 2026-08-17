"""Parent-loss watchdog: reap a sandbox container when its owner dies.

``docker run --rm`` cleans up when the CONTAINER's process exits. It does
nothing when the process that *launched* it dies instead — a SIGKILLed
host process leaves the container running with the task workspace
bind-mounted and credentials in its tmpfs, indefinitely.

The boot-time sweep (``reap_orphan_sandbox_containers``) catches this on
the next start, which can be hours later. This closes the window: a
separate process, in its own session, holds the read end of a pipe whose
write end lives in the owner. Nothing is ever written to that pipe in
normal operation — the kernel does the work, because when the owner dies
for ANY reason its file descriptors close and the read returns EOF. The
watchdog then verifies ownership and force-removes the container.

Its own session (``start_new_session``) matters: without it, a terminal
group signal aimed at the owner (Ctrl-C, a shell closing) would kill the
watchdog at exactly the moment it is needed.

Ownership is re-verified before removal, never assumed. The container is
identified by name, but a name can be reused, so removal only proceeds
when the container's ownership labels still match the ones recorded at
arm time — otherwise the watchdog would kill an innocent container that
happened to inherit the name.

Run as a module by ``arm_container_watchdog``; not a user-facing CLI.
"""
from __future__ import annotations

import argparse
import json
import os
import select
import subprocess
import sys
import time

DISARM_BYTE = b'\x01'
_REMOVE_ATTEMPTS = 5
_RETRY_SECONDS = 2.0
# How often to check whether the guarded container still exists. Long
# enough to be free at idle, short enough that watchdogs for finished
# containers do not pile up.
_IDLE_POLL_SECONDS = 30.0


def _container_labels(container: str) -> dict[str, str] | None:
    """Current labels of ``container``; ``None`` when it does not exist."""
    try:
        result = subprocess.run(
            ['docker', 'inspect', container, '--format', '{{json .Config.Labels}}'],
            capture_output=True, text=True,
            encoding='utf-8', errors='replace',
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        labels = json.loads(result.stdout.strip() or 'null')
    except ValueError:
        return None
    return labels if isinstance(labels, dict) else None


def _still_ours(
    container: str,
    owner_pid: str,
    owner_boot: str,
    pid_label: str,
    boot_label: str,
) -> bool:
    """Whether ``container`` is still the one we were armed for.

    A name can be reused by a later spawn. Killing on name alone would
    let a stale watchdog take down a healthy container belonging to
    someone else, so the recorded ownership labels must still match.

    The label KEYS are passed in rather than hardcoded: this lib carries
    no product branding of its own, and the caller already owns the
    label namespace.
    """
    labels = _container_labels(container)
    if labels is None:
        return False
    return (
        str(labels.get(pid_label, '')) == str(owner_pid)
        and str(labels.get(boot_label, '')) == str(owner_boot)
    )


def _remove(container: str) -> bool:
    try:
        result = subprocess.run(
            ['docker', 'rm', '--force', container],
            capture_output=True, text=True,
            encoding='utf-8', errors='replace',
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _write_incident(path: str, payload: dict) -> None:
    """Best-effort incident record. Never raises — this runs after the
    owner is already gone, so there is nobody left to report an error to
    except this file."""
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'a', encoding='utf-8') as stream:
            stream.write(json.dumps(payload, sort_keys=True) + '\n')
    except OSError:
        pass


def _wait_for_reap_trigger(
    read_fd: int,
    container: str,
    max_lifetime_seconds: float,
    now,
) -> str | None:
    """Block until something says "reap"; return why, or ``None`` to stand down.

    ``None`` means the watchdog should exit quietly — the owner disarmed
    (clean shutdown) or the container is already gone.
    """
    started = now()
    with os.fdopen(read_fd, 'rb', buffering=0) as pipe:
        while True:
            # Wake periodically even with nothing on the pipe, so a
            # watchdog whose container is already gone can retire itself.
            # Without this, every spawn would leave a process alive for
            # the whole life of the owner (one per container, forever),
            # since the common path never calls disarm.
            try:
                ready, _, _ = select.select([pipe], [], [], _IDLE_POLL_SECONDS)
            except (OSError, ValueError):
                return 'owner-process-lost'
            if not ready:
                if _container_labels(container) is None:
                    return None               # container gone; nothing to guard
                if max_lifetime_seconds and (now() - started) >= max_lifetime_seconds:
                    return 'max-lifetime-exceeded'
                continue
            try:
                chunk = pipe.read(1)
            except OSError:
                chunk = b''
            if chunk == DISARM_BYTE:
                return None                   # clean shutdown; owner handled it
            if chunk == b'':
                return 'owner-process-lost'   # EOF → the owner is gone
            # Any other byte: ignore, keep waiting. The channel carries
            # exactly one meaningful signal and unknown noise must not be
            # read as "the owner died" — that would kill a live container.


def watch(read_fd: int, container: str, owner_pid: str, owner_boot: str,
          incident_path: str = '', pid_label: str = '', boot_label: str = '',
          max_lifetime_seconds: float = 0.0, now=time.monotonic) -> int:
    """Block until the owner disarms or dies; reap on death. Returns rc.

    ``max_lifetime_seconds`` (0 disables) also reaps a container that
    outlives its cap even while its owner is perfectly healthy. A wedged
    agent — stuck in a retry loop, waiting on something that will never
    arrive — otherwise holds the workspace mount and its credentials
    open indefinitely, and nothing in the system notices because the
    owner never died.
    """
    reason = _wait_for_reap_trigger(
        read_fd, container, max_lifetime_seconds, now,
    )
    if reason is None:
        return 0

    if not _still_ours(container, owner_pid, owner_boot, pid_label, boot_label):
        return 0

    removed = False
    for attempt in range(_REMOVE_ATTEMPTS):
        # Retry: the docker daemon may be briefly unavailable (restart,
        # heavy load) exactly when a host is unhealthy enough to have
        # killed the owner in the first place.
        if _remove(container):
            removed = True
            break
        if attempt + 1 < _REMOVE_ATTEMPTS:
            time.sleep(_RETRY_SECONDS)

    still_present = _container_labels(container) is not None
    _write_incident(incident_path, {
        'container': container,
        'owner_pid': owner_pid,
        'owner_boot': owner_boot,
        'removed': removed,
        'verified_absent': not still_present,
        'reason': reason,
    })
    return 0 if removed and not still_present else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--fd', type=int, required=True)
    parser.add_argument('--container', required=True)
    parser.add_argument('--owner-pid', required=True)
    parser.add_argument('--owner-boot', default='')
    parser.add_argument('--incident-path', default='')
    parser.add_argument('--pid-label', required=True)
    parser.add_argument('--boot-label', required=True)
    parser.add_argument('--max-lifetime-seconds', type=float, default=0.0)
    args = parser.parse_args(argv)
    return watch(
        args.fd, args.container, args.owner_pid, args.owner_boot,
        args.incident_path, args.pid_label, args.boot_label,
        args.max_lifetime_seconds,
    )


if __name__ == '__main__':
    sys.exit(main())
