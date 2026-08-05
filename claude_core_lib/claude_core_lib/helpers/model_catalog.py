"""Discover current Claude model labels at runtime instead of hardcoding them.

The selectable model IDs are the stable ``claude`` CLI ALIASES — ``fable`` /
``opus`` / ``sonnet`` / ``haiku``. The CLI always resolves an alias to the
LATEST version (``--model`` help: "Provide an alias for the latest model (e.g.
'fable', 'opus', or 'sonnet')"), so the model the host actually runs can never
go stale. Only the human-facing LABEL can drift.

``fable`` is a GATED tier — the alias always resolves, but an account without
access gets "Claude Fable 5 is currently unavailable" at spawn. So it is only
OFFERED once a source positively confirms this host can run it.

We enrich the labels with the live version (e.g. "Opus 5") from three sources,
in order of authority:

1. the Anthropic models API, when a credential is available;
2. the ``claude`` CLI's OWN config cache (``~/.claude.json``) — credential-free
   AND account-scoped: the CLI stores the model options the server offered this
   account, so a model shows up here BEFORE it has ever been run locally;
3. the resolved model id the CLI already wrote into its own session logs (e.g.
   ``claude-opus-5``) — proof the host has actually run it.

Source 2 exists because 1 + 3 alone were a chicken-and-egg trap: with no API
credential (the common case — the operator is logged in via the CLI's keychain)
the ONLY signal was "a model I already ran", so a newly-released model could
never appear until it had somehow already been used, and the gated ``fable``
tier could never appear at all.

When no source yields a version we fall back to version-less labels ("Opus" /
"Sonnet" / "Haiku") — so the host never ships a hardcoded stale version like
"Opus 4.8". Best-effort and cached; never raises.
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
# model id. A family whose sessions all fall OUTSIDE this window gets no live
# label at all and silently renders version-less — with a few hundred logs on
# a busy host and a window of 80, that is exactly how the picker came to read
# "Opus 4.8 / Sonnet / Haiku": one family inside the window, the rest not.
# Widened, and only paid at all when the cheaper sources above didn't already
# cover every family (see ``_latest_by_family``).
_SESSION_LOG_SCAN_LIMIT = 200
# How far into each log to read before giving up on it. The resolved model id
# is written on the session's first assistant turn, so a small head is enough;
# the cap is what keeps a 30 MB transcript from being read end to end.
_LOG_HEAD_BYTES = 256 * 1024
# Minor is optional so ``claude-opus-5`` labels as "Opus 5"; an extra
# ``-<date>`` suffix (``claude-haiku-4-5-20251001``) or context-window marker
# (``claude-fable-5[1m]``) is ignored. The minor group caps at 3 digits WITH a
# trailing-digit boundary so a date directly after a no-minor major
# (``claude-sonnet-4-20250514`` — a real historical id shape) is recognised as
# a date, not parsed as minor 20250514 (which would outrank every genuine 4.x
# in the highest-version comparison and garble labels).
_MODEL_ID_RE = re.compile(r'^claude-(fable|opus|sonnet|haiku)-(\d+)(?:-(\d{1,3})(?!\d))?')
_FAMILIES = ('fable', 'opus', 'sonnet', 'haiku')

# The catalog changes only when Anthropic ships a model, so we cache — but with a
# TTL, not forever. A permanent process cache meant a freshly released version
# wouldn't show until the orchestrator was restarted; the TTL lets the label
# self-heal within minutes instead.
_CACHE_TTL_SECONDS = 600.0

# Selectable families, in display order. ``id`` is the stable CLI ALIAS we pass
# to ``claude --model`` — it always resolves to the latest of that family, so
# the version-less ``label`` is the guaranteed non-stale fallback and the id can
# never drift. ``gated`` marks a tier that needs account access (fable): the
# alias is valid, but offering it to an account that can't run it produces a
# spawn error, so it is shown only once discovery confirms it. ``default``
# mirrors the prior hardcoded default.
_ALIASES = (
    {'id': 'fable', 'label': 'Fable', 'family': 'fable', 'gated': True},
    {'id': 'opus', 'label': 'Opus', 'family': 'opus'},
    {'id': 'sonnet', 'label': 'Sonnet', 'family': 'sonnet', 'default': True},
    {'id': 'haiku', 'label': 'Haiku', 'family': 'haiku'},
)
_INTERNAL_KEYS = ('family', 'gated')


def _public_model(alias: dict) -> dict:
    """The picker-facing shape — ``family``/``gated`` are internal, drop them."""
    return {k: v for k, v in alias.items() if k not in _INTERNAL_KEYS}


# Fallback when discovery fails entirely (no credential, no CLI config, no
# session logs): the ungated aliases, which every account can resolve. The
# gated tier (fable) is omitted — offering a model we can't confirm is what
# produced the "Fable 5 unavailable" error; it reappears the moment discovery
# can vouch for it.
FALLBACK_MODELS = tuple(
    _public_model(a) for a in _ALIASES if not a.get('gated')
)

_cache: list[dict] | None = None
_cache_stamp: float = 0.0
_cache_lock = threading.Lock()


def discover_models(force: bool = False) -> list[dict]:
    """Return ``[{id, label[, default]}]`` for the composer's model picker.

    IDs are the stable CLI aliases; labels carry the live version from the
    Anthropic models API, else the CLI's own config cache, else the most recent
    CLI session log, else version-less. Cached with a short TTL so a
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
        live = live_by_family.get(alias['family'])
        if alias.get('gated') and not live:
            # Gated tier (Fable needs account access) — only OFFER it when a
            # source positively confirms this host can run it. The ungated
            # aliases always resolve, so they always show.
            continue
        model = _public_model(alias)
        if live:
            model['label'] = live
        out.append(model)
    return out


def _latest_by_family() -> dict[str, str]:
    """Map family → its newest model's label (e.g. ``{'opus': 'Opus 5'}``).

    Sources are consulted in order of authority — the models API (account-scoped
    and authoritative) wins, then the CLI's own config cache, then the session
    logs — and each only fills families the previous ones did not cover. We stop
    as soon as every family is covered: the log scan opens hundreds of files
    (~1-2s on a busy host) and there is nothing left for it to answer once the
    cheaper sources have. Returns ``{}`` only when every source is empty, so
    callers fall back to the version-less entries. Best-effort — never raises.
    """
    latest = _labels_from_models_api()
    for source in (_labels_from_cli_config, _labels_from_session_logs):
        if len(latest) == len(_FAMILIES):
            break
        try:
            found = source()
        except Exception:
            continue
        for family, label in found.items():
            latest.setdefault(family, label)
    return latest


def _highest_per_family(model_ids) -> dict[str, str]:
    """Family → label of the HIGHEST-version id seen in ``model_ids``.

    Highest-version rather than first-seen: no source guarantees ordering (the
    API's ordering is not contractual, the CLI's caches are keyed arbitrarily,
    and session logs are ordered by mtime), and picking the wrong one silently
    downgrades the label — the "still shows Opus 4.7 after resuming an old
    session" regression.
    """
    best: dict[str, tuple[int, int, str]] = {}
    for model_id in model_ids:
        parsed = family_version_from_model_id(model_id)
        if parsed is None:
            continue
        family, major, minor, label = parsed
        current = best.get(family)
        if current is None or (major, minor) > current[:2]:
            best[family] = (major, minor, label)
    return {family: value[2] for family, value in best.items()}


def _labels_from_models_api() -> dict[str, str]:
    """Family → label from the Anthropic models API (``{}`` if no credential)."""
    data = _fetch_models_api()
    if not data:
        return {}
    return _highest_per_family(
        str(entry.get('id') or '') for entry in data if isinstance(entry, dict)
    )


def _labels_from_cli_config() -> dict[str, str]:
    """Family → label from the ``claude`` CLI's own config cache.

    Credential-free AND account-scoped. The CLI records, in ``~/.claude.json``,
    the model options the server offered this account (``additionalModelOptionsCache``)
    and the model of each cached client-data slot (``clientDataCacheSlots``).
    That makes a newly-released or gated model (fable) discoverable BEFORE it
    has ever been run on this host — which the API (needs a credential) and the
    session logs (need a prior run) cannot do.

    This reads the CLI's private on-disk shape, so every access is defensive and
    a miss is just an empty dict — the other sources still apply. Only model-id
    strings are read; nothing else in the file is touched.
    """
    path = _cli_config_path()
    try:
        with path.open('r', encoding='utf-8') as handle:
            config = json.load(handle)
    except Exception:
        return {}
    if not isinstance(config, dict):
        return {}
    return _highest_per_family(_model_ids_in_cli_config(config))


def _model_ids_in_cli_config(config: dict) -> list[str]:
    """Every model-id string the CLI cached in its config (best-effort).

    Three places, all in the one already-parsed file: the model options the
    server offered this account, the model of each cached client-data slot, and
    the per-project usage ledger. Reading all three is free next to the session
    log walk, and each covers families the others miss.
    """
    return [
        *_values_of(config.get('additionalModelOptionsCache'), 'value'),
        *_values_of(_dict_values(config.get('clientDataCacheSlots')), 'model'),
        *_usage_keys(config.get('projects')),
    ]


def _dict_values(value) -> list:
    """``dict`` → its values; anything else → ``[]``."""
    return list(value.values()) if isinstance(value, dict) else []


def _values_of(entries, key: str) -> list[str]:
    """``key`` off every dict in ``entries`` (non-dicts and non-lists ignored)."""
    if not isinstance(entries, list):
        return []
    return [str(e.get(key) or '') for e in entries if isinstance(e, dict)]


def _usage_keys(projects) -> list[str]:
    """Model ids from every project's ``lastModelUsage`` ledger."""
    ids: list[str] = []
    for project in _dict_values(projects):
        usage = project.get('lastModelUsage') if isinstance(project, dict) else None
        if isinstance(usage, dict):
            ids.extend(str(key) for key in usage)
    return ids


def _cli_config_path() -> Path:
    """Where the ``claude`` CLI keeps its config JSON.

    ``CLAUDE_CONFIG_DIR`` (the CLI's own override) relocates it; otherwise it is
    ``~/.claude.json`` — a sibling of ``~/.claude``, not inside it.
    """
    config_dir = (os.environ.get('CLAUDE_CONFIG_DIR') or '').strip()
    base = Path(config_dir) if config_dir else Path.home()
    return base / '.claude.json'


def _labels_from_session_logs() -> dict[str, str]:
    """Family → label from resolved ids in the CLI's session logs.

    Credential-free: the ``claude`` CLI records the concrete model it resolved an
    alias to (e.g. ``claude-opus-5``) on every assistant turn it writes to
    ``<config>/projects/**/*.jsonl``. We scan the most recently touched logs and,
    per family, keep the **highest version** seen — NOT whichever log was touched
    most recently. The alias always resolves to the latest, so the right label is
    the newest version the host has actually run; selecting by mtime would wrongly
    downgrade opus the moment an old session is resumed/re-touched.
    Returns ``{}`` on any failure (no logs, unreadable, parse error).
    """
    try:
        logs = _recent_session_logs()
    except Exception:
        return {}
    seen: list[str] = []
    for log in logs:
        try:
            seen.extend(_model_ids_in_log(log))
        except Exception:
            continue
    return _highest_per_family(seen)


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


def _model_ids_in_log(log: Path) -> list[str]:
    """The model ids resolved in ``log`` — one per family (``[]`` if none).

    First occurrence per family wins within a file (a session runs one model); the
    cheap ``'claude-'`` substring pre-check skips the JSON parse on lines that can't
    hold a model id, and the scan stops once every family is seen.

    Reading is capped at ``_LOG_HEAD_BYTES``. Without it, a single-model session
    never satisfies the all-families stop condition, so it was read to EOF —
    tens of MB per transcript — hunting for families that were never in it. The
    model id appears on the session's first assistant turn, so the head is where
    the answer always is; the rest was pure I/O.
    """
    found: dict[str, str] = {}
    read = 0
    with log.open('r', encoding='utf-8') as handle:
        for line in handle:
            read += len(line)
            if 'claude-' in line:
                model_id = _model_id_of_event(_loads_or_empty(line))
                parsed = family_version_from_model_id(model_id)
                if parsed and parsed[0] not in found:
                    found[parsed[0]] = model_id
                    if len(found) == len(_FAMILIES):
                        break
            if read >= _LOG_HEAD_BYTES:
                break
    return list(found.values())


def _loads_or_empty(line: str) -> dict:
    try:
        event = json.loads(line)
    except Exception:
        return {}
    return event if isinstance(event, dict) else {}


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
    keychain) — then the credential-free sources supply the labels.
    """
    api_key = (os.environ.get('ANTHROPIC_API_KEY') or '').strip()
    if api_key:
        return {'x-api-key': api_key}
    oauth_token = (os.environ.get('CLAUDE_CODE_OAUTH_TOKEN') or '').strip()
    if oauth_token:
        return {'Authorization': f'Bearer {oauth_token}'}
    return None
