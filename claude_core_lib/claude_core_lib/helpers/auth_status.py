"""Is the Claude CLI signed in?

``claude auth status --json`` answers with ``loggedIn``, and exits non-zero when
it is false. Asked with no terminal (``stdin`` closed): given one, the CLI can
switch it to raw mode and leave it that way. Only whether it is signed in, and
how, is kept — the answer also names the account, which nobody here needs.

``logged_in`` is None when the CLI cannot tell: an older CLI with no ``auth``
command, one that did not answer in time, or an answer that is not the JSON
this expects. A caller must never read None as "signed out".
"""

from __future__ import annotations

import json
import subprocess

AUTH_STATUS_TIMEOUT_SECONDS = 20.0


def claude_login_status(binary: str = 'claude', *, timeout: float = AUTH_STATUS_TIMEOUT_SECONDS) -> dict:
    """``{'logged_in': bool | None, 'method': str}``."""
    try:
        result = subprocess.run(
            [binary or 'claude', 'auth', 'status', '--json'],
            capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return {'logged_in': None, 'method': ''}
    try:
        answer = json.loads(result.stdout)
    except ValueError:
        return {'logged_in': None, 'method': ''}
    if not isinstance(answer, dict) or not isinstance(answer.get('loggedIn'), bool):
        return {'logged_in': None, 'method': ''}
    method = answer.get('authMethod')
    return {'logged_in': answer['loggedIn'], 'method': method if isinstance(method, str) else ''}
