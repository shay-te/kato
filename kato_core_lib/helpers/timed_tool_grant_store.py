"""Time-boxed tool approvals — "Allow for 10 min".

The middle ground between the two buttons that already exist. "Allow once"
re-asks on every command, which for a task that runs docker in a loop is a
stream of interruptions the operator stops reading. "Allow always" is a
persisted, global grant, which is too much to hand over for a burst of work.

A timed grant covers ONE working burst and then expires on its own:

* **In memory only.** Nothing is written to disk, so a kato restart drops
  every grant. A permission the operator has forgotten about must not
  outlive the process they granted it in — that is the whole difference
  between this and ``tool_decision_store``.
* **Keyed exactly like a remembered decision** — tool name plus the command's
  PROGRAM (``docker``), never the verbatim line. Approving ``docker compose
  up`` covers later docker commands and nothing else.
* **Allow only.** There is no timed deny: a deny that silently lapses would
  re-run the thing the operator refused.

Eligibility is deliberately NARROW (see ``timed_grant_eligible``) — this is
not a general "stop asking me" switch.
"""
from __future__ import annotations

import os
import threading
import time

# The one duration the UI offers. A single value on purpose: a duration
# picker on a security prompt invites the wrong choice, and the button says
# the real number so the operator always knows what they granted.
#
# Ten minutes covers a build-run-check cycle — the actual unit of work — and
# expires before the operator has walked away from it.
GRANT_MINUTES = 10

# Programs a timed grant may cover. Docker because a containerised task runs
# it in a loop; the network tools because research is the same shape — many
# calls, one intent.
_ELIGIBLE_PROGRAMS = frozenset({
    'docker', 'docker-compose', 'docker-buildx', 'podman',
})
_ELIGIBLE_TOOLS = frozenset({'WebFetch', 'WebSearch'})

# May a timed grant cover a command that reaches OUTSIDE the task folder?
#
# Default OFF — the safe posture, and the one every existing install keeps
# without doing anything.
#
# It exists because the default made the feature useless for the operator who
# asked for it: they run ``docker run -v /host/path:/data``, the absolute path
# trips the out-of-workspace check, and both durable scopes are withheld — so
# the button they asked for never appeared on the only commands they wanted it
# for. "still i see only allow once on the docker run."
#
# What this switch does NOT do, at any setting:
#
# * It never widens WHICH requests are eligible. Only docker/podman and the
#   network tools can ever receive a timed grant (``timed_grant_eligible``),
#   with or without this.
# * It never enables a REMEMBERED ("Allow always") approval out of workspace.
#   A persisted, global, restart-surviving grant for something reaching
#   outside the sandbox is a different class of risk from a ten-minute window
#   the operator is sitting in front of, and it stays refused.
# * It never overrides a high-risk Action Guard category (credential_read,
#   network_exfil, remote_exec, sandbox_escape).
#
# So the widest this reaches is: ten minutes, in memory, for docker — gone on
# restart, gone when the window lapses.
_OUTSIDE_WORKSPACE_ENV = 'KATO_TIMED_GRANT_OUTSIDE_WORKSPACE'


def timed_grant_allowed_outside_workspace() -> bool:
    """Is the operator opting in to timed grants for out-of-folder commands?

    Read on every call rather than cached at import: the Settings UI writes
    the value and the operator expects the next prompt to honour it, not the
    next restart.
    """
    raw = str(os.environ.get(_OUTSIDE_WORKSPACE_ENV, '') or '').strip().lower()
    return raw in ('1', 'true', 'yes', 'on')


_lock = threading.Lock()
# {(tool_name, program): expiry_epoch}
_grants: dict[tuple[str, str], float] = {}


def timed_grant_eligible(tool_name: str, programs) -> bool:
    """May this request be granted for a window rather than once/always?

    Narrow by design. Everything else keeps the existing two choices — a
    timed grant is a convenience for repetitive, low-surprise commands, not
    a way to pre-approve whatever the agent does next.

    The CALLER still applies the safety carve-outs that gate "Allow always"
    (high-risk Action Guard categories, out-of-sandbox paths). A time-boxed
    sandbox escape is still a sandbox escape.
    """
    tool = str(tool_name or '').strip()
    # No tool, no eligibility. ``grant_for`` already refuses a blank name, but
    # the predicate is what the UI asks — it must not offer a button for a
    # request the store would then decline to record.
    if not tool:
        return False
    if tool in _ELIGIBLE_TOOLS:
        return True
    return any(
        str(program or '').strip().lower() in _ELIGIBLE_PROGRAMS
        for program in (programs or [])
    )


def grant_for(tool_name: str, programs, minutes: float = GRANT_MINUTES) -> int:
    """Allow ``tool_name`` for each eligible program for ``minutes``.

    Returns how many grants were recorded — 0 when nothing was eligible, so
    the caller can tell "granted" from "silently ignored".
    """
    tool = str(tool_name or '').strip()
    if not tool or minutes <= 0:
        return 0
    expiry = time.time() + (float(minutes) * 60.0)
    recorded = 0
    with _lock:
        if tool in _ELIGIBLE_TOOLS:
            _grants[(tool, '')] = expiry
            recorded += 1
        for program in (programs or []):
            name = str(program or '').strip()
            if name.lower() in _ELIGIBLE_PROGRAMS:
                _grants[(tool, name)] = expiry
                recorded += 1
    return recorded


def timed_grant_active(tool_name: str, programs) -> bool:
    """True when EVERY program in the request is inside a live grant.

    Every one, not any: ``docker build … && rm -rf /`` must not ride in on
    the docker grant. A chain is only covered when the operator has approved
    each program in it.
    """
    tool = str(tool_name or '').strip()
    if not tool:
        return False
    now = time.time()
    with _lock:
        _drop_expired(now)
        if tool in _ELIGIBLE_TOOLS:
            return (tool, '') in _grants
        names = [str(p or '').strip() for p in (programs or [])]
        if not names:
            return False
        return all((tool, name) in _grants for name in names)


def active_grants() -> list[dict[str, object]]:
    """Live grants as ``[{tool_name, program, seconds_remaining}]``."""
    now = time.time()
    with _lock:
        _drop_expired(now)
        return sorted(
            (
                {
                    'tool_name': tool,
                    'program': program,
                    'seconds_remaining': int(max(0, expiry - now)),
                }
                for (tool, program), expiry in _grants.items()
            ),
            key=lambda entry: (entry['tool_name'], entry['program']),
        )


def clear_timed_grants() -> None:
    """Drop every grant — the Settings panel's "revoke now"."""
    with _lock:
        _grants.clear()


def _drop_expired(now: float) -> None:
    """Caller holds ``_lock``."""
    for key in [key for key, expiry in _grants.items() if expiry <= now]:
        del _grants[key]
