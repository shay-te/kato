"""Discover the live OpenRouter model catalog so the settings UI never hardcodes
a stale slug.

OpenRouter is reached as an OpenHands LLM gateway: the operator stores a model
string like ``openrouter/openai/gpt-4o`` in ``OPENHANDS_LLM_MODEL``. Rather than
make them remember an exact slug (which drifts as OpenRouter adds/retires models),
we pull the catalog from OpenRouter's public ``/v1/models`` endpoint (no API key
required to list) and offer it as autocomplete on that field.

Each entry is ``{'id': 'openrouter/<slug>', 'label': '<display name>'}`` — the
``id`` is exactly what belongs in ``OPENHANDS_LLM_MODEL``. Best-effort and cached;
falls back to a small static set when offline; never raises.
"""
from __future__ import annotations

import json
import threading
import urllib.request

OPENROUTER_MODELS_URL = 'https://openrouter.ai/api/v1/models'
_PREFIX = 'openrouter/'

# Fallback only — used when the OpenRouter API can't be reached (offline, tests).
# Matches the slugs the settings field documents as examples; live discovery
# supersedes it whenever the network is available.
FALLBACK_OPENROUTER_MODELS = (
    {'id': 'openrouter/openai/gpt-4o', 'label': 'OpenAI: GPT-4o'},
    {'id': 'openrouter/anthropic/claude-3.5-haiku', 'label': 'Anthropic: Claude 3.5 Haiku'},
    {'id': 'openrouter/google/gemini-2.0-flash-001', 'label': 'Google: Gemini 2.0 Flash'},
)

_cache: list[dict] | None = None
_cache_lock = threading.Lock()


def discover_openrouter_models() -> list[dict]:
    """Return ``[{id, label}]`` for the OpenRouter model autocomplete.

    ``id`` is the full ``openrouter/<slug>`` string the operator stores; ``label``
    is OpenRouter's display name. Live from the public catalog, cached process-wide
    (it changes rarely; a restart re-discovers). Always non-empty, never raises.
    """
    global _cache
    with _cache_lock:
        if _cache is not None:
            return [dict(m) for m in _cache]
    models = _fetch_catalog() or [dict(m) for m in FALLBACK_OPENROUTER_MODELS]
    with _cache_lock:
        _cache = models
    return [dict(m) for m in models]


def reset_openrouter_models_cache() -> None:
    """Clear the discovery cache (tests / a catalog refresh mid-process)."""
    global _cache
    with _cache_lock:
        _cache = None


def _fetch_catalog(timeout: float = 6.0) -> list[dict] | None:
    """Fetch + parse the public catalog; ``None`` on any failure."""
    try:
        request = urllib.request.Request(
            OPENROUTER_MODELS_URL, headers={'User-Agent': 'kato'},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode('utf-8'))
    except Exception:
        return None
    rows = payload.get('data') if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return None
    models: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        entry = _entry_from_row(row)
        if entry and entry['id'] not in seen:
            seen.add(entry['id'])
            models.append(entry)
    return models or None


def _entry_from_row(row: object) -> dict | None:
    """``{'id': 'openai/gpt-4o', 'name': 'OpenAI: GPT-4o'}`` → option, or ``None``."""
    if not isinstance(row, dict):
        return None
    slug = str(row.get('id') or '').strip()
    if not slug:
        return None
    label = str(row.get('name') or '').strip() or slug
    return {'id': f'{_PREFIX}{slug}', 'label': label}
