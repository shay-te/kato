"""Discover current Claude model labels at runtime instead of hardcoding them.

The selectable model IDs are the stable ``claude`` CLI ALIASES — ``opus`` /
``sonnet`` / ``haiku``. The CLI always resolves an alias to the LATEST version
(``--model`` help: "Provide an alias for the latest model"), so the model the host
actually runs can never go stale. Only the human-facing LABEL can drift.

``fable`` (the tier above opus) has no CLI alias yet, so it is served as a
pinned FULL model id (``claude-fable-5``); discovery upgrades the pin itself —
id and label together — when a newer fable model appears in either source.

We enrich the labels with the live version (e.g. "Opus 4.8") from two sources, in
order of authority:

1. the Anthropic models API, when a credential is available;
2. the resolved model id the CLI already wrote into its own session logs (e.g.
   ``claude-opus-4-8``) — a credential-free, on-disk source that reflects exactly
   what the CLI resolves each alias to right now.

When neither yields a version we fall back to version-less labels ("Opus" /
"Sonnet" / "Haiku") — so the host never ships a hardcoded stale version like
"Opus 4.7". Best-effort and cached; never raises.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.request
from pathlib import Path

ANTHROPIC_MODELS_URL = 'https://api.anthropic.com/v1/models?limit=100'
_ANTHROPIC_VERSION = '2023-06-01'

# How many of the most-recently-touched session logs to scan for a resolved
# model id. One match per family is enough (a session runs a single model), so a
# small window covers all three families on any active install while bounding I/O.
_SESSION_LOG_SCAN_LIMIT = 80
# Minor is optional so a future ``claude-opus-5`` still labels as "Opus 5"; an
# extra ``-<date>`` suffix (``claude-haiku-4-5-20251001``) or context-window
# marker (``claude-fable-5[1m]``) is ignored. The minor group caps at 3
# digits WITH a trailing-digit boundary so a date directly after a no-minor
# major (``claude-sonnet-4-20250514`` — a real historical id shape) is
# recognised as a date, not parsed as minor 20250514 (which would outrank
# every genuine 4.x in the highest-version comparison and garble labels —
# and, for fable, the pinned id itself).
_MODEL_ID_RE = re.compile(r'^claude-(fable|opus|sonnet|haiku)-(\d+)(?:-(\d{1,3})(?!\d))?')
_FAMILIES = ('fable', 'opus', 'sonnet', 'haiku')

# The catalog changes only when Anthropic ships a model, so we cache — but with a
# TTL, not forever. A permanent process cache meant a freshly released version
# (or a newly-run one picked up from the session logs) wouldn't show until kato
# was restarted; the TTL lets the label self-heal within minutes instead.
_CACHE_TTL_SECONDS = 600.0

# Selectable families, in display order. ``id`` is what we pass to
# ``claude --model``; ``family`` is the discovery key. For opus/sonnet/haiku the
# id is the stable CLI ALIAS (always resolves to the latest), so the version-less
# ``label`` is the guaranteed non-stale fallback. Fable has NO CLI alias yet
# (``--model fable`` is rejected), so its id is a pinned FULL model id — the
# label matches that pinned id exactly, and discovery upgrades both id and label
# together when a newer fable model shows up (API or session logs), so neither
# can drift from the other. ``default`` mirrors the prior hardcoded default.
_ALIASES = (
    {'id': 'claude-fable-5', 'label': 'Fable 5', 'family': 'fable'},
    {'id': 'opus', 'label': 'Opus', 'family': 'opus'},
    {'id': 'sonnet', 'label': 'Sonnet', 'family': 'sonnet', 'default': True},
    {'id': 'haiku', 'label': 'Haiku', 'family': 'haiku'},
)


def _public_model(alias: dict) -> dict:
    """The picker-facing shape — ``family`` is internal, drop it."""
    return {k: v for k, v in alias.items() if k != 'family'}


# Fallback when discovery fails entirely (no credential AND no session logs):
# only the stable CLI aliases, which always resolve. Pinned/gated entries
# (fable) are omitted — offering a model we can't confirm is available is what
# produced the "Fable 5 unavailable" error; it reappears the moment discovery
# can vouch for it.
FALLBACK_MODELS = tuple(
    _public_model(a) for a in _ALIASES if a['id'] == a['family']
)

_cache: list[dict] | None = None
_cache_stamp: float = 0.0
_cache_lock = threading.Lock()


def discover_models(force: bool = False) -> list[dict]:
    """Return ``[{id, label[, default]}]`` for the composer's model picker.

    IDs are the stable aliases; labels carry the live version when the Anthropic
    models API is reachable with the host's configured credential, else from the
    most recent CLI session log, else version-less. Cached with a short TTL so a
    newly-released version surfaces without a restart. ``force`` bypasses the
    TTL (the UI's explicit refresh) so a just-installed CLI's labels show
    immediately. Always non-empty, never raises.
    """
    global _cache, _cache_stamp
    now = time.monotonic()
    with _cache_lock:
        if not force and _cache is not None and (now - _cache_stamp) < _CACHE_TTL_SECONDS:
            return [dict(m) for m in _cache]
    models = _aliases_with_live_labels()
    with _cache_lock:
        _cache = models
        _cache_stamp = time.monotonic()
    return [dict(m) for m in models]


def reset_models_cache() -> None:
    """Clear the discovery cache (tests / a model-list change mid-process)."""
    global _cache, _cache_stamp
    with _cache_lock:
        _cache = None
        _cache_stamp = 0.0


def _aliases_with_live_labels() -> list[dict]:
    live_by_family = _latest_by_family()
    out: list[dict] = []
    for alias in _ALIASES:
        pinned = alias['id'] != alias['family']  # fable: a pinned full id, gated
        live = live_by_family.get(alias['family'])
        if pinned and not live:
            # Gated/pinned model (Fable needs Mythos access) — only OFFER it
            # when discovery (models API or this host's session logs) positively
            # confirms the account can run it. Otherwise selecting it errors
            # ("Claude Fable 5 is currently unavailable"). The CLI aliases
            # (opus/sonnet/haiku) are always resolvable, so they always show.
            continue
        model = _public_model(alias)
        if live:
            model['label'] = live['label']
            if pinned:
                # Track the newest discovered id so the pin self-heals when a
                # newer model of that family ships.
                model['id'] = live['model_id']
        out.append(model)
    return out


def _latest_by_family() -> dict[str, dict]:
    """Map family → ``{label, model_id}`` for its newest model (e.g. "Opus 4.8").

    The authoritative Anthropic models API wins per family; any family it doesn't
    cover (e.g. no credential) is filled from the CLI's own session logs. Returns
    ``{}`` only when both sources are empty, so callers fall back to the static
    entries. Best-effort — never raises.
    """
    latest = _labels_from_models_api()
    for family, live in _labels_from_session_logs().items():
        latest.setdefault(family, live)
    return latest


def _labels_from_models_api() -> dict[str, dict]:
    """Family → ``{label, model_id}`` from the Anthropic models API (``{}`` if no credential)."""
    data = _fetch_models_api()
    if not data:
        return {}
    latest: dict[str, dict] = {}
    # ``data`` is newest-first, so the first match per family is the latest.
    for entry in data:
        if not isinstance(entry, dict):
            continue
        model_id = str(entry.get('id') or '')
        display = str(entry.get('display_name') or '')
        parsed = family_version_from_model_id(model_id)
        if parsed is None or not display:
            continue
        family = parsed[0]
        if family not in latest:
            latest[family] = {
                'label': _strip_claude_prefix(display),
                'model_id': _clean_model_id(parsed),
            }
    return latest


def _labels_from_session_logs() -> dict[str, dict]:
    """Family → ``{label, model_id}`` from resolved ids in the CLI's session logs.

    Credential-free: the ``claude`` CLI records the concrete model it resolved an
    alias to (e.g. ``claude-opus-4-8``) on every assistant turn it writes to
    ``<config>/projects/**/*.jsonl``. We scan the most recently touched logs and,
    per family, keep the **highest version** seen — NOT whichever log was touched
    most recently. The alias always resolves to the latest, so the right label is
    the newest version the host has actually run; selecting by mtime would wrongly
    downgrade opus back to 4.7 the moment an old 4.7 session is resumed/re-touched.
    Returns ``{}`` on any failure (no logs, unreadable, parse error).
    """
    best: dict[str, tuple] = {}
    try:
        logs = _recent_session_logs()
    except Exception:
        return {}
    for log in logs:
        try:
            found = _model_versions_in_log(log)
        except Exception:
            continue
        for family, candidate in found.items():
            current = best.get(family)
            # Compare on (major, minor); keep the higher version's label.
            if current is None or candidate[:2] > current[:2]:
                best[family] = candidate
    return {
        family: {
            'label': candidate[2],
            'model_id': _clean_model_id((family,) + candidate),
        }
        for family, candidate in best.items()
    }


def _clean_model_id(parsed: tuple) -> str:
    """``("opus", 4, 8, "Opus 4.8")`` → ``"claude-opus-4-8"``.

    Date/context suffixes never reach here — ``_MODEL_ID_RE`` refuses to
    parse a date segment as the minor (see its comment), so the rebuilt id
    is always the bare family-major[-minor] form. A no-minor id
    (``("fable", 5, 0, "Fable 5")``) keeps the short form ``claude-fable-5``:
    the label carries a ``.`` only when the source id had a real minor
    segment, so an x.0 release ("Sonnet 5.0") still round-trips.
    """
    family, major, minor, label = parsed
    return f'claude-{family}-{major}-{minor}' if '.' in label else f'claude-{family}-{major}'


def _recent_session_logs() -> list[Path]:
    """The most-recently-modified Claude session JSONLs, newest first (capped).

    Honours ``CLAUDE_CONFIG_DIR`` (the CLI's own override) and defaults to
    ``~/.claude`` — the same place the CLI writes its logs.
    """
    config_dir = (os.environ.get('CLAUDE_CONFIG_DIR') or '').strip()
    base = Path(config_dir) if config_dir else Path.home() / '.claude'
    projects = base / 'projects'
    if not projects.is_dir():
        return []
    logs = [p for p in projects.glob('*/*.jsonl') if p.is_file()]
    logs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return logs[:_SESSION_LOG_SCAN_LIMIT]


def _model_versions_in_log(log: Path) -> dict[str, tuple[int, int, str]]:
    """Map each family resolved in ``log`` to ``(major, minor, label)`` (``{}`` if none).

    First occurrence per family wins within a file (a session runs one model); the
    cheap ``'claude-'`` substring pre-check skips the JSON parse on lines that can't
    hold a model id, and the scan stops once every family is seen so a long
    transcript rarely costs more than its opening turns.
    """
    found: dict[str, tuple[int, int, str]] = {}
    with log.open('r', encoding='utf-8') as handle:
        for line in handle:
            if 'claude-' not in line:
                continue
            try:
                event = json.loads(line)
            except Exception:
                continue
            parsed = family_version_from_model_id(_model_id_of_event(event))
            if parsed and parsed[0] not in found:
                family, major, minor, label = parsed
                found[family] = (major, minor, label)
                if len(found) == len(_FAMILIES):
                    break
    return found


def _model_id_of_event(event: dict) -> str:
    """Pull the model id off an event, whether top-level or under ``message``."""
    if not isinstance(event, dict):
        return ''
    model = event.get('model')
    if not model:
        message = event.get('message')
        model = message.get('model') if isinstance(message, dict) else ''
    return str(model or '')


def family_version_from_model_id(model_id: str) -> tuple[str, int, int, str] | None:
    """``"claude-opus-4-8"`` → ``("opus", 4, 8, "Opus 4.8")``; ``None`` if not a real id.

    The numeric ``(major, minor)`` lets callers pick the highest version; the label
    matches the API's "Family X.Y" style. A missing minor sorts as 0 ("Opus 5");
    trailing date segments (``claude-haiku-4-5-20251001``) are ignored.
    """
    match = _MODEL_ID_RE.match(model_id or '')
    if not match:
        return None
    family, major, minor = match.group(1), match.group(2), match.group(3)
    version = f'{major}.{minor}' if minor is not None else major
    return family, int(major), int(minor) if minor is not None else 0, \
        f'{family.capitalize()} {version}'


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
