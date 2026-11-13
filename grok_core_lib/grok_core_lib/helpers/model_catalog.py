"""Discover the Grok models the operator's CLI offers.

``grok models`` prints the account's auth status, the default model, and then
the models this login can use::

    You are logged in with …
    Default model: grok-4.5
    Available models:
      * grok-4.5 (default)
      - grok-4.6

Reading it gives the ids the operator's own login can actually run, so the
host never hardcodes a stale one. The binary is ``$GROK_CLI_BINARY`` when the
host sets it, else ``grok`` on PATH. Best-effort and cached (a short TTL —
the list changes only when the account or the CLI does); never raises, never
empty.
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
import time

# Fallback only — the models Grok Build documents, used when ``grok models``
# can't be run (offline tests, CLI not installed).
FALLBACK_MODELS = (
    {'id': 'grok-4.5', 'label': 'Grok 4.5', 'default': True},
    {'id': 'grok-4.6', 'label': 'Grok 4.6'},
    {'id': 'grok-4.7', 'label': 'Grok 4.7'},
)

_BINARY_ENV_KEY = 'GROK_CLI_BINARY'
_TTL_SECONDS = 300.0
_TIMEOUT_SECONDS = 15.0
_LINE = re.compile(r'^\s*[*-]\s+(?P<id>[A-Za-z0-9][A-Za-z0-9._:/-]*)(?P<default>\s+\(default\))?\s*$')

_cache: dict[str, tuple[float, list[dict]]] = {}
_cache_lock = threading.Lock()


def discover_models(force: bool = False) -> list[dict]:
    """``[{id, label[, default]}]`` for the Grok model picker. Never raises, never empty.

    ``force`` re-reads even inside the cache window. Signature-identical to
    every other transport's ``discover_models``.
    """
    binary = os.environ.get(_BINARY_ENV_KEY, '').strip() or 'grok'
    now = time.monotonic()
    if not force:
        with _cache_lock:
            cached = _cache.get(binary)
            if cached is not None and now - cached[0] < _TTL_SECONDS:
                return [dict(model) for model in cached[1]]
    models = _listed_models(binary) or [dict(model) for model in FALLBACK_MODELS]
    with _cache_lock:
        _cache[binary] = (now, [dict(model) for model in models])
    return [dict(model) for model in models]


def _listed_models(binary: str) -> list[dict] | None:
    try:
        proc = subprocess.run(
            [binary, 'models'],
            capture_output=True, text=True, timeout=_TIMEOUT_SECONDS, check=False,
            stdin=subprocess.DEVNULL,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    return _parse_models(proc.stdout or '')


def _parse_models(text: str) -> list[dict] | None:
    """The models under ``Available models:`` in ``grok models`` output, or None."""
    _, found, listed = str(text or '').partition('Available models:')
    if not found:
        return None
    models: list[dict] = []
    for line in listed.splitlines():
        match = _LINE.match(line)
        if not match:
            continue
        model = {'id': match.group('id'), 'label': _label(match.group('id'))}
        if match.group('default'):
            model['default'] = True
        models.append(model)
    return models or None


def _label(model_id: str) -> str:
    """``grok-4.6`` → ``Grok 4.6``; anything else as written."""
    head, _, version = model_id.partition('-')
    if head.lower() == 'grok' and version:
        return f'Grok {version}'
    return model_id


def reset_models_cache() -> None:
    """Clear the discovery cache (tests / a CLI catalog refresh mid-process)."""
    with _cache_lock:
        _cache.clear()


def discover_pinned_models(force: bool = False) -> list[dict]:
    """The pinned versions beyond ``discover_models`` — none for Grok.

    Every transport answers this. A Grok model id already names its exact
    version, and ``discover_models`` lists them all.
    """
    del force
    return []
