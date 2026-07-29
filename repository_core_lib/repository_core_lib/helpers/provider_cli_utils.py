"""Reuse the provider CLI login the developer already has.

``gh`` and ``glab`` hold a live API token for a signed-in developer, and
``gh auth token`` simply prints it. Anyone running an autonomous coding
agent has almost certainly already authenticated one of these — so the
tool can ask the CLI at the moment it needs a token instead of asking a
human to mint a personal access token and paste it into a config file.

Reading it at use time (rather than copying it into config) is the point:
rotation and expiry then take care of themselves, and no long-lived
secret is written to disk by us.

Every probe is non-raising and bounded by a timeout — "CLI not
installed", "not signed in" and "CLI wedged" are one answer to the
caller: an empty string.
"""

from __future__ import annotations

import re
import shutil
import subprocess

from git_core_lib.git_core_lib.helpers.text_utils import normalized_text


CLI_TIMEOUT_SECONDS = 15

# provider -> the CLI that owns its login.
PROVIDER_CLI_BINARIES = {
    'github': 'gh',
    'gitlab': 'glab',
}

# "✓ Logged in to github.com account octocat (keyring)" — and the older
# "as octocat" phrasing. Parsed from `auth status` because it is OFFLINE;
# `gh api user` would be exact but costs a network round trip on a screen
# the operator is waiting on. A miss just means no name is shown.
_ACCOUNT_PATTERN = re.compile(
    r'Logged in to\s+\S+\s+(?:account|as)\s+([A-Za-z0-9][\w.-]*)',
)


def provider_cli_binary(provider: str) -> str:
    """The CLI name for ``provider``, or '' when it has none."""
    return PROVIDER_CLI_BINARIES.get(normalized_text(provider).lower(), '')


def provider_cli_installed(provider: str) -> bool:
    """True when the provider's CLI is on PATH."""
    binary = provider_cli_binary(provider)
    return bool(binary) and bool(shutil.which(binary))


def _run_cli(binary: str, args: list[str]) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(
            [binary, *args],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            check=False,
            timeout=CLI_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _hostname_args(hostname: str) -> list[str]:
    host = normalized_text(hostname)
    return ['--hostname', host] if host else []


def read_provider_cli_token(provider: str, hostname: str = '') -> str:
    """The live API token the provider's CLI holds, or ''.

    ``hostname`` targets a self-hosted instance (GitHub Enterprise,
    self-managed GitLab); omit it for the public host.
    """
    binary = provider_cli_binary(provider)
    if not binary or not shutil.which(binary):
        return ''
    result = _run_cli(binary, ['auth', 'token', *_hostname_args(hostname)])
    if result is None or result.returncode != 0:
        return ''
    return result.stdout.strip()


def read_provider_cli_account(provider: str, hostname: str = '') -> str:
    """The account name the CLI is signed in as, or '' if unreadable.

    Cosmetic — it lets the UI say *which* login it found. Never treat a
    miss as "not signed in"; only the token answers that.
    """
    binary = provider_cli_binary(provider)
    if not binary or not shutil.which(binary):
        return ''
    result = _run_cli(binary, ['auth', 'status', *_hostname_args(hostname)])
    if result is None:
        return ''
    # gh has moved this line between stdout and stderr across versions.
    match = _ACCOUNT_PATTERN.search(f'{result.stdout}\n{result.stderr}')
    return match.group(1) if match else ''
