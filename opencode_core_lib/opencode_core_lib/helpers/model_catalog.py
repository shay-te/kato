"""Discover the OpenCode models the operator's providers offer.

``opencode models`` prints one ``provider/model`` id per line, for every
provider the operator has connected — the ids ``opencode run -m`` takes. The
binary is ``$OPENCODE_CLI_BINARY`` when the host sets it, else ``opencode``.
Best-effort and cached for a short window (the list changes only when a
provider is connected or the catalog refreshes); never raises, never empty.
"""

from __future__ import annotations

import re
import threading
import time

from opencode_core_lib.opencode_core_lib.session.history import (
    opencode_binary,
    run_store_command,
)

# Fallback only — used when ``opencode models`` can't be run (offline tests,
# CLI not installed): well-known ``provider/model`` ids. Discovery from the
# operator's own providers supersedes it.
FALLBACK_MODELS = (
    {'id': 'anthropic/claude-sonnet-4-5', 'label': 'anthropic/claude-sonnet-4-5', 'default': True},
    {'id': 'openai/gpt-5', 'label': 'openai/gpt-5'},
    {'id': 'google/gemini-2.5-pro', 'label': 'google/gemini-2.5-pro'},
)

_TTL_SECONDS = 300.0
_MODEL_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._:/@-]*$')

_cache: dict[str, tuple[float, list[dict]]] = {}
_cache_lock = threading.Lock()


def discover_models(force: bool = False) -> list[dict]:
    """``[{id, label}]`` for the OpenCode model picker. Never raises, never empty.

    ``force`` re-reads even inside the cache window. Signature-identical to
    every other transport's ``discover_models``.
    """
    binary = opencode_binary()
    now = time.monotonic()
    if not force:
        with _cache_lock:
            cached = _cache.get(binary)
            if cached is not None and now - cached[0] < _TTL_SECONDS:
                return [dict(model) for model in cached[1]]
    models = _parse_models(run_store_command(['models'], binary=binary)) or [
        dict(model) for model in FALLBACK_MODELS
    ]
    with _cache_lock:
        _cache[binary] = (now, [dict(model) for model in models])
    return [dict(model) for model in models]


def _parse_models(text: str) -> list[dict] | None:
    models = []
    for line in str(text or '').splitlines():
        model_id = line.strip()
        if _MODEL_ID.match(model_id):
            models.append({'id': model_id, 'label': model_id})
    return models or None


def reset_models_cache() -> None:
    """Clear the discovery cache (tests / a CLI catalog refresh mid-process)."""
    with _cache_lock:
        _cache.clear()


def discover_pinned_models(force: bool = False) -> list[dict]:
    """The pinned versions beyond ``discover_models`` — none for OpenCode.

    Every transport answers this. An OpenCode id already names the exact
    provider model, and ``discover_models`` lists them all.
    """
    del force
    return []
