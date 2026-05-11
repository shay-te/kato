"""Provider-agnostic URL and credential-message utilities for VCS providers."""
from __future__ import annotations

from urllib.parse import urlparse

from git_core_lib.git_core_lib.helpers.repository_discovery_utils import remote_web_base_url
from git_core_lib.git_core_lib.helpers.text_utils import text_from_attr


def provider_from_url_string(url: str) -> str:
    """Infer the VCS provider name from a URL or base-URL string."""
    normalized = url.lower()
    if 'bitbucket' in normalized:
        return 'bitbucket'
    if 'github' in normalized:
        return 'github'
    if 'gitlab' in normalized:
        return 'gitlab'
    return ''


def default_provider_base_url(provider: str, remote_url: str) -> str:
    """Return the canonical API base URL for a provider given its remote URL."""
    web_base_url = remote_web_base_url(remote_url)
    if not web_base_url:
        return ''
    host = str(urlparse(web_base_url).hostname or '').lower()
    if provider == 'github':
        if host == 'github.com':
            return 'https://api.github.com'
        return f'{web_base_url}/api/v3'
    if provider == 'gitlab':
        return f'{web_base_url}/api/v4'
    if provider == 'bitbucket' and host == 'bitbucket.org':
        return 'https://api.bitbucket.org/2.0'
    return ''


def fallback_web_base_url(repository) -> str:
    """Compute a web base URL from a repository's remote_url or provider_base_url."""
    remote_url = text_from_attr(repository, 'remote_url')
    if remote_url:
        return remote_web_base_url(remote_url)
    provider_base_url = text_from_attr(repository, 'provider_base_url')
    if not provider_base_url:
        return ''
    if 'api.bitbucket.org' in provider_base_url:
        return 'https://bitbucket.org'
    if provider_base_url.rstrip('/').endswith('/api/v4'):
        return provider_base_url[: -len('/api/v4')]
    if provider_base_url.rstrip('/').endswith('/api/v3'):
        return provider_base_url[: -len('/api/v3')]
    if provider_base_url.rstrip('/').endswith('/api'):
        return provider_base_url[: -len('/api')]
    return provider_base_url


def missing_pull_request_token_message(repository_id: str, provider: str) -> str:
    """Return an actionable error message when a PR API token is missing."""
    env_key = {
        'github': 'GITHUB_API_TOKEN',
        'gitlab': 'GITLAB_API_TOKEN',
        'bitbucket': 'BITBUCKET_API_TOKEN',
    }.get(provider, '<provider-token>')
    return (
        f'missing pull request API token for repository {repository_id}; '
        f'set {env_key} or configure repository token explicitly'
    )
