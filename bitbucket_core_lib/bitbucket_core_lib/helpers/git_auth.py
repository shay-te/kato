"""Git HTTP authentication helpers for Bitbucket and other VCS providers.

Constructs the ``Authorization`` header that must be injected into git
subprocess environments when the repository uses an HTTPS remote with
token-based auth.
"""
from __future__ import annotations

from urllib.parse import urlparse

from bitbucket_core_lib.bitbucket_core_lib.client.auth import basic_auth_header

_PROVIDER_DEFAULT_USERNAMES: dict[str, str] = {
    'github': 'x-access-token',
    'gitlab': 'oauth2',
    'bitbucket': 'x-token-auth',
}

BITBUCKET_USERNAME_ATTR = 'bitbucket_username'


def git_http_auth_header(
    repository,
    *,
    bitbucket_username_attr: str = BITBUCKET_USERNAME_ATTR,
) -> str:
    """Return a ``Authorization: Basic …`` header for an HTTP git remote.

    Returns an empty string when the remote is SSH, when no token is
    configured, or when no username can be resolved.
    """
    if repository is None:
        return ''
    remote_url = _attr(repository, 'remote_url')
    if not _is_http_remote(remote_url):
        return ''
    token = _attr(repository, 'token')
    if not token:
        return ''
    username = git_http_username(
        repository, remote_url, bitbucket_username_attr=bitbucket_username_attr
    )
    if not username:
        return ''
    return f'Authorization: {basic_auth_header(username, token)}'


def git_http_username(
    repository,
    remote_url: str,
    *,
    bitbucket_username_attr: str = BITBUCKET_USERNAME_ATTR,
) -> str:
    """Resolve the HTTP username for a given repository and remote URL."""
    parsed = urlparse(remote_url)
    provider = _attr(repository, 'provider').lower()
    if provider == 'bitbucket':
        username = _attr(repository, bitbucket_username_attr) or _attr(repository, 'username')
        if username:
            return username
        return parsed.username or 'x-token-auth'
    if parsed.username:
        return parsed.username
    return _PROVIDER_DEFAULT_USERNAMES.get(provider, 'git')


def _attr(obj: object, key: str) -> str:
    return str(getattr(obj, key, '') or '').strip()


def _is_http_remote(remote_url: str) -> bool:
    normalized = remote_url.lower()
    return normalized.startswith('https://') or normalized.startswith('http://')
