"""Find the provider credential the operator ALREADY has, so they don't paste one.

Operator feedback, verbatim: *"api key is prehistoric"*. And they were
right — anyone running an autonomous coding agent has already
authenticated to their code host at least once. Kato should look before
it asks.

The ladder, cheapest first. The first source that yields a token wins;
if none do, the wizard's paste form is still there, unchanged:

1. ``cli``            — ``gh auth token`` / ``glab auth token``
2. ``git-credential`` — git's own helper (Keychain / Credential Manager /
   libsecret), populated the first time they pushed over HTTPS
3. ``environment``    — ``GH_TOKEN`` / ``GITHUB_TOKEN`` / ``GITLAB_TOKEN``,
   already exported in plenty of dev shells

WHAT GETS STORED IS THE **SOURCE**, NOT THE SECRET. Picking a source
writes ``<PROVIDER>_API_TOKEN_SOURCE=cli`` to ``settings.json`` and
leaves ``<PROVIDER>_API_TOKEN`` empty; the token is resolved at boot (and
re-resolved on a short TTL) from the live source. So rotation and expiry
handle themselves, and kato writes no long-lived secret to disk — which
is strictly better than the paste path it replaces.

Precedence is unchanged: a token exported in the real shell still wins
over everything here, exactly like every other setting.

This module is the kato-side GLUE — it knows kato's key names and its
settings file. The reusable probes live in the libs
(``git_core_lib.helpers.git_credential_utils``,
``repository_core_lib.helpers.provider_cli_utils``).
"""

from __future__ import annotations

import os
import threading
import time
from urllib.parse import urlsplit

from git_core_lib.git_core_lib.helpers.git_credential_utils import read_git_credential
from repository_core_lib.repository_core_lib.helpers.provider_cli_utils import (
    provider_cli_binary,
    read_provider_cli_account,
    read_provider_cli_token,
)


SOURCE_CLI = 'cli'
SOURCE_GIT_CREDENTIAL = 'git-credential'
SOURCE_ENVIRONMENT = 'environment'
SOURCE_PASTED = 'pasted'

# Suffix of the settings key that records WHICH source a provider uses.
SOURCE_KEY_SUFFIX = '_API_TOKEN_SOURCE'
TOKEN_KEY_SUFFIX = '_API_TOKEN'

# Providers whose credential can be discovered at all. Jira and YouTrack
# have neither a CLI nor a git-credential entry (they are trackers, not
# code hosts), so they keep the paste form — the honest answer, rather
# than an empty picker that looks broken.
DISCOVERABLE_PROVIDERS = ('github', 'gitlab', 'bitbucket')

_DEFAULT_HOSTS = {
    'github': 'github.com',
    'gitlab': 'gitlab.com',
    'bitbucket': 'bitbucket.org',
}

# Conventional env vars for each provider, in the order the ecosystem
# prefers them (gh reads GH_TOKEN before GITHUB_TOKEN).
_ENVIRONMENT_KEYS = {
    'github': ('GH_TOKEN', 'GITHUB_TOKEN'),
    'gitlab': ('GITLAB_TOKEN',),
    'bitbucket': ('BITBUCKET_TOKEN',),
}

# Probing shells out (gh + git). ``effective_config_env`` is called on
# every config-status poll, so an uncached resolve would spawn processes
# on a UI timer. Short TTL: long enough to absorb the polls, short enough
# that `gh auth refresh` in another terminal is picked up promptly.
_CACHE_TTL_SECONDS = 60.0

_cache: dict[tuple[str, str, str], tuple[float, str]] = {}
_cache_lock = threading.Lock()


def token_key(provider: str) -> str:
    """``'github'`` → ``'GITHUB_API_TOKEN'``."""
    return f'{str(provider or "").strip().upper()}{TOKEN_KEY_SUFFIX}'


def source_key(provider: str) -> str:
    """``'github'`` → ``'GITHUB_API_TOKEN_SOURCE'``."""
    return f'{str(provider or "").strip().upper()}{SOURCE_KEY_SUFFIX}'


def base_url_key(provider: str) -> str:
    return f'{str(provider or "").strip().upper()}_API_BASE_URL'


def host_for_provider(provider: str, base_url: str = '') -> str:
    """The hostname to ask about — from the configured API base URL when
    set (GitHub Enterprise / self-managed GitLab), else the public host.
    """
    name = str(provider or '').strip().lower()
    host = ''
    if base_url:
        host = urlsplit(str(base_url).strip()).hostname or ''
        # api.github.com → github.com: the credential helper and `gh`
        # are keyed by the WEB host, not the API subdomain.
        if host.startswith('api.'):
            host = host[len('api.'):]
    return host or _DEFAULT_HOSTS.get(name, '')


def clear_cache() -> None:
    """Drop every cached token (tests, and after an explicit re-check)."""
    with _cache_lock:
        _cache.clear()


def _cached(provider: str, source: str, host: str, produce) -> str:
    key = (provider, source, host)
    now = time.monotonic()
    with _cache_lock:
        entry = _cache.get(key)
        if entry and entry[0] > now:
            return entry[1]
    value = produce()
    with _cache_lock:
        _cache[key] = (now + _CACHE_TTL_SECONDS, value)
    return value


def _environment_token(provider: str) -> tuple[str, str]:
    """``(token, env_key)`` from the conventional env vars, or ``('', '')``."""
    for key in _ENVIRONMENT_KEYS.get(provider, ()):
        value = str(os.environ.get(key, '') or '').strip()
        if value:
            return value, key
    return '', ''


def resolve_credential_token(provider: str, source: str, base_url: str = '') -> str:
    """The live token for ``provider`` from ``source``; '' when unavailable.

    Cached briefly (see ``_CACHE_TTL_SECONDS``). ``SOURCE_PASTED`` — and
    any unknown source — resolves to '' so the caller falls back to the
    stored value.
    """
    name = str(provider or '').strip().lower()
    kind = str(source or '').strip().lower()
    if not name or kind in ('', SOURCE_PASTED):
        return ''
    host = host_for_provider(name, base_url)
    if kind == SOURCE_CLI:
        return _cached(name, kind, host, lambda: read_provider_cli_token(name, host))
    if kind == SOURCE_GIT_CREDENTIAL:
        return _cached(name, kind, host, lambda: read_git_credential(host)[1])
    if kind == SOURCE_ENVIRONMENT:
        # Never cached: an env var is free to read and the operator can
        # change it between polls.
        return _environment_token(name)[0]
    return ''


def discover_credential_sources(provider: str, base_url: str = '') -> list[dict]:
    """Every source that can supply a token for ``provider``, right now.

    Returns UI-shaped dicts — ``{id, label, detail, account}`` — and
    **never the token itself**: the value goes to the client only as
    "yes, this works", never as a secret the browser could leak.
    """
    name = str(provider or '').strip().lower()
    if name not in DISCOVERABLE_PROVIDERS:
        return []
    host = host_for_provider(name, base_url)
    found: list[dict] = []

    binary = provider_cli_binary(name)
    if binary and resolve_credential_token(name, SOURCE_CLI, base_url):
        account = read_provider_cli_account(name, host)
        found.append({
            'id': SOURCE_CLI,
            'label': f'{binary} CLI login',
            'account': account,
            'detail': (
                f'Signed in as {account}' if account
                else f'The {binary} CLI on this machine is signed in'
            ),
        })

    username, secret = read_git_credential(host)
    if secret:
        found.append({
            'id': SOURCE_GIT_CREDENTIAL,
            'label': 'Saved git credential',
            'account': username,
            'detail': (
                f'Stored for {host}'
                + (f' as {username}' if username else '')
                + ' by your system credential helper'
            ),
        })

    _, env_key = _environment_token(name)
    if env_key:
        found.append({
            'id': SOURCE_ENVIRONMENT,
            'label': f'${env_key}',
            'account': '',
            'detail': f'{env_key} is already exported in kato\'s environment',
        })
    return found


def resolved_credential_env(settings) -> dict[str, str]:
    """``{TOKEN_KEY: token}`` for every provider configured to use a source.

    Only fills a token that is EMPTY in ``settings`` — an explicitly
    pasted token, or one exported in the shell, still wins. A source that
    resolves to nothing (CLI signed out since setup) yields no key, so
    the normal "missing credential" path reports it instead of kato
    booting with a silently empty token.
    """
    mapping = dict(settings or {})
    out: dict[str, str] = {}
    for provider in DISCOVERABLE_PROVIDERS:
        source = str(mapping.get(source_key(provider), '') or '').strip()
        if not source or source == SOURCE_PASTED:
            continue
        key = token_key(provider)
        if str(mapping.get(key, '') or '').strip():
            continue
        token = resolve_credential_token(
            provider, source, str(mapping.get(base_url_key(provider), '') or ''),
        )
        if token:
            out[key] = token
    return out
