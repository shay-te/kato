"""Discover current Claude model labels at runtime instead of hardcoding them.

The selectable model IDs are the stable ``claude`` CLI ALIASES — ``opus`` /
``sonnet`` / ``haiku``. The CLI always resolves an alias to the LATEST version
(``--model`` help: "Provide an alias for the latest model"), so the model the host
actually runs can never go stale. Only the human-facing LABEL can drift.

We enrich the labels with the live version (e.g. "Opus 4.8") from the Anthropic
models API when a credential is available, and fall back to version-less labels
("Opus" / "Sonnet" / "Haiku") otherwise — so the host never ships a hardcoded stale
version like "Opus 4.7". Best-effort and cached; never raises.
"""
from __future__ import annotations

import json
import os
import threading
import urllib.request

ANTHROPIC_MODELS_URL = 'https://api.anthropic.com/v1/models?limit=100'
_ANTHROPIC_VERSION = '2023-06-01'

# Alias families, in display order. ``id`` is what we pass to ``claude --model``
# (always resolves to the latest); the version-less ``label`` is the guaranteed
# non-stale fallback. ``default`` mirrors the prior hardcoded default (sonnet).
_ALIASES = (
    {'id': 'opus', 'label': 'Opus'},
    {'id': 'sonnet', 'label': 'Sonnet', 'default': True},
    {'id': 'haiku', 'label': 'Haiku'},
)
FALLBACK_MODELS = tuple(dict(a) for a in _ALIASES)

_cache: list[dict] | None = None
_cache_lock = threading.Lock()


def discover_models() -> list[dict]:
    """Return ``[{id, label[, default]}]`` for the composer's model picker.

    IDs are the stable aliases; labels carry the live version when the Anthropic
    models API is reachable with the host's configured credential, else version-less.
    Cached process-wide (the catalog changes rarely; a restart re-discovers).
    Always non-empty, never raises.
    """
    global _cache
    with _cache_lock:
        if _cache is not None:
            return [dict(m) for m in _cache]
    models = _aliases_with_live_labels()
    with _cache_lock:
        _cache = models
    return [dict(m) for m in models]


def reset_models_cache() -> None:
    """Clear the discovery cache (tests / a model-list change mid-process)."""
    global _cache
    with _cache_lock:
        _cache = None


def _aliases_with_live_labels() -> list[dict]:
    labels = _latest_labels_by_family()
    out: list[dict] = []
    for alias in _ALIASES:
        model = dict(alias)
        live = labels.get(alias['id'])
        if live:
            model['label'] = live
        out.append(model)
    return out


def _latest_labels_by_family() -> dict[str, str]:
    """Map alias family → its newest display label (e.g. ``opus`` → "Opus 4.8").

    Best-effort: returns ``{}`` on any failure (no credential, offline, parse
    error) so callers fall back to the version-less labels.
    """
    data = _fetch_models_api()
    if not data:
        return {}
    labels: dict[str, str] = {}
    # ``data`` is newest-first, so the first match per family is the latest.
    for entry in data:
        if not isinstance(entry, dict):
            continue
        model_id = str(entry.get('id') or '')
        display = str(entry.get('display_name') or '')
        for family in ('opus', 'sonnet', 'haiku'):
            if family in labels:
                continue
            if model_id.startswith(f'claude-{family}-') and display:
                labels[family] = _strip_claude_prefix(display)
    return labels


def _strip_claude_prefix(display: str) -> str:
    # "Claude Opus 4.8" → "Opus 4.8" to match the host's existing label style.
    prefix = 'Claude '
    return display[len(prefix):] if display.startswith(prefix) else display


def _fetch_models_api(timeout: float = 3.0) -> list | None:
    headers = _auth_headers()
    if headers is None:
        return None
    headers['anthropic-version'] = _ANTHROPIC_VERSION
    try:
        request = urllib.request.Request(ANTHROPIC_MODELS_URL, headers=headers)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode('utf-8'))
    except Exception:
        return None
    data = payload.get('data') if isinstance(payload, dict) else None
    return data if isinstance(data, list) else None


def _auth_headers() -> dict | None:
    """Pick the auth header from whichever Claude credential the host has, if any.

    Pay-per-token ``ANTHROPIC_API_KEY`` uses ``x-api-key``; a Max/Pro
    ``CLAUDE_CODE_OAUTH_TOKEN`` uses ``Authorization: Bearer``. Returns ``None``
    when neither is set (e.g. the operator is logged in via the CLI's own
    keychain) — then we serve version-less labels rather than guessing.
    """
    api_key = (os.environ.get('ANTHROPIC_API_KEY') or '').strip()
    if api_key:
        return {'x-api-key': api_key}
    oauth_token = (os.environ.get('CLAUDE_CODE_OAUTH_TOKEN') or '').strip()
    if oauth_token:
        return {'Authorization': f'Bearer {oauth_token}'}
    return None
