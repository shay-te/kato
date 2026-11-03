"""The real host behind an SSH remote, which the remote may not name.

``git@work:team/api.git`` goes wherever ``Host work`` in the operator's SSH
config says: the usual way to hold two accounts on one host. Git never needs to
know, but a web or API address built from that remote does. ``work`` is not a
host, so the provider stayed unknown, and ``git@github-work:...`` was recognised
and then given the API address ``https://github-work/api/v3``.

``ssh -G`` prints the configuration ssh would use, and connects to nothing.
"""

from __future__ import annotations

import re
import shutil
import subprocess

from git_core_lib.git_core_lib.helpers.repository_discovery_utils import remote_parts


SSH_LOOKUP_TIMEOUT_SECONDS = 5

# What a host offers networks that block SSH: the same service on port 443.
_PORT_443_HOSTS = {
    'ssh.github.com': 'github.com',
    'altssh.gitlab.com': 'gitlab.com',
    'altssh.bitbucket.org': 'bitbucket.org',
}
_PUBLIC_HOSTS = frozenset(_PORT_443_HOSTS.values())

# Only what a host name can be: this comes out of a remote, and it goes on a
# command line, where a leading dash would be an option.
_HOST_NAME = re.compile(r'[a-z0-9][a-z0-9._-]*')

_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def ssh_host_name(alias: str) -> str:
    """The host ssh connects to for ``alias``; ``alias`` if it cannot tell."""
    ssh = shutil.which('ssh')
    if not ssh or not _HOST_NAME.fullmatch(alias):
        return alias
    try:
        result = subprocess.run(
            [ssh, '-G', alias],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=SSH_LOOKUP_TIMEOUT_SECONDS,
            creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return alias
    if result.returncode != 0:
        return alias
    for line in result.stdout.splitlines():
        key, _, value = line.partition(' ')
        if key == 'hostname' and value.strip():
            return value.strip().lower()
    return alias


def remote_with_real_host(remote_url: str) -> str:
    """``remote_url`` naming the real host, to build web and API addresses from.

    Never for git itself: the configured remote stays as the operator wrote it,
    because a push compares it with the clone's own.
    """
    host, path, scheme, _ = remote_parts(remote_url)
    if not host or scheme not in ('', 'ssh') or host in _PUBLIC_HOSTS:
        return remote_url
    real_host = ssh_host_name(host)
    real_host = _PORT_443_HOSTS.get(real_host, real_host)
    if real_host == host:
        return remote_url
    return f'ssh://{real_host}/{path.lstrip("/")}'
