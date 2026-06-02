"""Discover current Codex models from the codex CLI's own catalog cache.

The ``codex`` CLI has no "list models" subcommand, but it maintains an
etag-validated catalog at ``$CODEX_HOME/models_cache.json`` (``$CODEX_HOME``
defaults to ``~/.codex``). Reading it gives the live model slugs + display
names that reflect the operator's actual codex routing/auth, so the host never
hardcodes a stale slug. Falls back to a static set when the file is missing or
unreadable. Best-effort and cached (keyed on the file path + mtime); never
raises.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

# Fallback only — used when the codex cache file can't be read/parsed (offline
# tests, codex not yet run). Matches the catalog shipped at the time of writing;
# discovery from the live cache supersedes it.
FALLBACK_CODEX_MODELS = (
    {'id': 'gpt-5.5', 'label': 'GPT-5.5', 'default': True},
    {'id': 'gpt-5.4', 'label': 'GPT-5.4'},
    {'id': 'gpt-5.4-mini', 'label': 'GPT-5.4-Mini'},
)

_cache: dict[str, tuple[int, list[dict]]] = {}
_cache_lock = threading.Lock()


def codex_models_cache_path() -> Path:
    """Path to the codex CLI's model cache, honouring ``$CODEX_HOME``."""
    home = os.environ.get('CODEX_HOME') or str(Path.home() / '.codex')
    return Path(home) / 'models_cache.json'


def discover_codex_models() -> list[dict]:
    """Return ``[{id, label[, default]}]`` for the codex model picker.

    Parsed from the codex cache (slug → id, display_name → label), keeping only
    user-listable models supported in the API, ordered by codex's own priority.
    Cached on (path, mtime) so a refreshed cache is picked up. Always non-empty,
    never raises.
    """
    path = codex_models_cache_path()
    key = str(path)
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        mtime = 0
    with _cache_lock:
        cached = _cache.get(key)
        if cached is not None and cached[0] == mtime:
            return [dict(m) for m in cached[1]]
    models = _parse_cache(path) or [dict(m) for m in FALLBACK_CODEX_MODELS]
    with _cache_lock:
        _cache[key] = (mtime, [dict(m) for m in models])
    return [dict(m) for m in models]


def reset_codex_models_cache() -> None:
    """Clear the discovery cache (tests / a CLI catalog refresh mid-process)."""
    with _cache_lock:
        _cache.clear()


def _parse_cache(path: Path) -> list[dict] | None:
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None
    rows = payload.get('models') if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return None
    listed = [
        row for row in rows
        if isinstance(row, dict)
        and row.get('visibility') == 'list'  # hide internal/hidden models
        and row.get('supported_in_api')
        and row.get('slug')
    ]
    # codex orders the catalog by ``priority`` ascending (the active default is
    # the lowest); preserve that so the first entry is the sensible default.
    listed.sort(key=lambda row: row.get('priority', 1_000_000))
    out: list[dict] = []
    for index, row in enumerate(listed):
        slug = str(row['slug'])
        model = {'id': slug, 'label': str(row.get('display_name') or slug)}
        if index == 0:
            model['default'] = True
        out.append(model)
    return out or None
