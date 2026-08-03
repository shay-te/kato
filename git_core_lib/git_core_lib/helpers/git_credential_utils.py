"""Read a credential the operator's git already has stored.

``git credential fill`` is git's own lookup: it asks whatever helper the
operator configured (macOS Keychain, Windows Credential Manager,
libsecret, ``store``) for the username/secret of a host and prints them
on stdout. Anyone who has ever pushed to that host over HTTPS already
has an entry — so a tool that needs an API token for the SAME host can
reuse it instead of asking a human to mint and paste one.

Hardened like every other git call here — built through
``build_safe_git_command``, so hooks are disabled — plus three things
specific to asking for secrets in the background:

* ``GIT_TERMINAL_PROMPT=0`` and ``GCM_INTERACTIVE=never``: a helper that
  WOULD have prompted must fail instead of hanging a caller that has no
  terminal attached (a webserver thread, a scan loop).
* a timeout, because a wedged credential helper is a real thing.
* run from the operator's HOME, never a repository, so a repo-local
  ``credential.helper`` — which a hostile clone can set — is not what
  answers the question.

Never raises. "Nothing stored", "no helper configured" and "git is not
installed" are the same answer to a caller: ``('', '')``.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from git_core_lib.git_core_lib.helpers.git_command_utils import build_safe_git_command
from utils_core_lib.utils_core_lib.text_utils import normalized_text


CREDENTIAL_TIMEOUT_SECONDS = 10

# Anything that could turn a background lookup into an interactive
# prompt. Applied on top of the caller's environment, never replacing it
# (the helper needs the real HOME / PATH / keychain session to work).
_NON_INTERACTIVE_ENV = {
    'GIT_TERMINAL_PROMPT': '0',
    'GCM_INTERACTIVE': 'never',
}


def parse_credential_output(text: str) -> tuple[str, str]:
    """``(username, secret)`` from ``git credential fill``'s key=value output.

    Unknown keys are ignored; a missing password yields ``''`` so the
    caller's "did I get a secret" check stays a simple truthiness test.
    """
    username = ''
    secret = ''
    for line in str(text or '').splitlines():
        key, separator, value = line.partition('=')
        if not separator:
            continue
        if key == 'username':
            username = value.strip()
        elif key == 'password':
            secret = value.strip()
    return username, secret


def read_git_credential(
    host: str,
    *,
    protocol: str = 'https',
    working_directory: str = '',
    timeout_seconds: int = CREDENTIAL_TIMEOUT_SECONDS,
) -> tuple[str, str]:
    """Return ``(username, secret)`` git has stored for ``host``.

    ``('', '')`` when there is nothing stored, no helper configured, or
    git is unavailable — the caller treats all three the same way (fall
    through to the next credential source).
    """
    host_text = normalized_text(host)
    if not host_text:
        return '', ''
    directory = normalized_text(working_directory) or str(Path.home())
    command = build_safe_git_command(directory, ['credential', 'fill'])
    request = f'protocol={normalized_text(protocol) or "https"}\nhost={host_text}\n\n'
    try:
        result = subprocess.run(
            command,
            input=request,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            check=False,
            timeout=timeout_seconds,
            env={**os.environ, **_NON_INTERACTIVE_ENV},
        )
    except (OSError, subprocess.SubprocessError):
        return '', ''
    if result.returncode != 0:
        return '', ''
    return parse_credential_output(result.stdout)
