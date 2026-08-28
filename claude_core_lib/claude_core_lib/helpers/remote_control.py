"""Remote Control: hand a live local session to claude.ai / the Claude app.

Claude Code can expose a session it is already running so the same
conversation can be picked up from another device — the CLI calls this
**Remote Control** (``/remote-control`` in the REPL, ``--remote-control``
when starting an interactive one, and the ``remoteControlAtStartup``
setting for "every session"). The session keeps running on THIS machine;
the other device is only a remote.

A host driving ``claude -p --output-format stream-json --input-format
stream-json`` has no REPL and no interactive flag, so neither of those two
entry points applies. What it has instead is the control channel both
sides already share: the same wire the CLI uses to ask permission is
bidirectional, and the host can send ``{"subtype": "remote_control",
"enabled": true|false}`` on it at any point in a session's life. That is
the path the first-party IDE hosts take, and it is the one
:meth:`StreamingClaudeSession.set_remote_control` implements.

Two things follow from "at any point", and both are the opposite of how
``--model`` / ``--effort`` / ``--permission-mode`` behave here:

* it applies to the RUNNING subprocess — no respawn, no lost context;
* it dies with that subprocess — the bridge is not re-established for
  free on the next spawn, so a caller that wants the preference to
  outlive a respawn has to re-send it (this lib deliberately stores no
  preference of its own; that is the host's business).
"""

from __future__ import annotations

import re
import subprocess
import threading

#: ``subtype`` of the control request that toggles the bridge.
REMOTE_CONTROL_SUBTYPE = 'remote_control'

#: How long to wait for the CLI's answer. Enabling is not local work — the
#: CLI registers the bridge with the service before it can hand back a URL —
#: so this is sized for a slow network, not for a local round trip. A caller
#: serving an HTTP request blocks for it, which is the reason it is not
#: larger: a refusal (not signed in, unknown subtype) comes back fast, and
#: the timeout only covers the case where nothing comes back at all.
REMOTE_CONTROL_TIMEOUT_SECONDS = 30.0

#: The "not connected" state, and the shape every state dict has.
#: ``session_url`` is the page to open on the other device; ``connect_url``
#: is the environment-level entry point the CLI hands back alongside it.
REMOTE_CONTROL_OFF: dict = {
    'enabled': False,
    'session_url': '',
    'connect_url': '',
    'bridge_session_id': '',
}

_support_cache: dict[str, bool] = {}
_support_cache_lock = threading.Lock()


def remote_control_state(response: dict | None) -> dict:
    """Normalise a ``remote_control`` control-response body into a state dict.

    The enable reply carries ``session_url`` / ``connect_url`` /
    ``bridge_session_id`` (plus fields we don't surface); the disable reply
    carries no body at all. Returning the same keys either way keeps every
    caller — and the JSON that reaches a UI — free of ``None`` branches.
    """
    if not isinstance(response, dict):
        return dict(REMOTE_CONTROL_OFF)
    return {
        'enabled': True,
        'session_url': str(response.get('session_url', '') or ''),
        'connect_url': str(response.get('connect_url', '') or ''),
        'bridge_session_id': str(response.get('bridge_session_id', '') or ''),
    }


def supports_remote_control(binary: str = 'claude', timeout: float = 10.0) -> bool:
    """Whether the installed CLI knows about Remote Control at all.

    Probed from ``<binary> --help`` rather than compared against a hardcoded
    version floor: a floor is a guess that goes stale in both directions
    (it nags installs that are fine and stays quiet on ones that aren't),
    while the help text is the CLI's own statement about itself. Mirrors
    ``helpers/effort_levels.py``, including the per-binary cache — the help
    output cannot change without the binary changing.

    Conservative on failure: a CLI we cannot probe is reported as NOT
    supporting it, so the toggle stays hidden instead of offering an
    operator a switch that silently does nothing.
    """
    key = str(binary or 'claude').strip() or 'claude'
    with _support_cache_lock:
        if key in _support_cache:
            return _support_cache[key]
    supported = _probe_help_for_remote_control(key, timeout)
    with _support_cache_lock:
        _support_cache[key] = supported
    return supported


def _probe_help_for_remote_control(binary: str, timeout: float) -> bool:
    try:
        proc = subprocess.run(
            [binary, '--help'],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except Exception:
        return False
    text = f'{proc.stdout or ""}\n{proc.stderr or ""}'
    return bool(re.search(r'--remote-control\b', text))


def reset_remote_control_support_cache() -> None:
    """Clear the probe cache (tests / a CLI upgrade mid-process)."""
    with _support_cache_lock:
        _support_cache.clear()
