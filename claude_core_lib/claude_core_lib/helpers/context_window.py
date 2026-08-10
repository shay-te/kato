"""How much context a model has, and how much of it a turn used.

Feeds the composer's context indicator. The operator watches this to decide
when to ``/compact`` — so the numbers have to be honest: an unknown window is
reported as ``0`` (render "unknown"), never as a guess, because a wrong
"93% full" pushes someone into compacting a session that had plenty of room,
and a wrong "40%" lets one hit the wall mid-task.
"""

from __future__ import annotations

import re

# Prompt-side usage keys. Together these are the conversation as the model saw
# it this turn: fresh input plus whatever was served from / written to cache.
# ``output_tokens`` is deliberately absent — what the model wrote lands in the
# NEXT turn's prompt, so counting it here would double-count it.
_CONTEXT_USAGE_KEYS = (
    'input_tokens',
    'cache_read_input_tokens',
    'cache_creation_input_tokens',
)

# The two window sizes current Claude models actually ship.
_SHORT_CONTEXT_TOKENS = 200_000
_LONG_CONTEXT_TOKENS = 1_000_000

# Some hosts append an explicit long-context marker to the model id
# (``claude-fable-5[1m]`` is a real selectable value in the CLI's own model
# options cache). When it is present it is authoritative — but it is NOT
# present on the id the stream reports back, which is why it can never be the
# only signal. See ``context_window_tokens``.
_LONG_CONTEXT_MARKERS = ('[1m]', '-1m', ':1m')

# Model families, and the first (major, minor) release in each whose window is
# 1M rather than 200k. ``None`` means "no release of this family is 1M".
#
# Keyed by FAMILY + VERSION rather than by exact model id: a full id table goes
# stale the day a model ships and then reports a wrong window, which is the one
# failure this indicator cannot afford. A family's window, by contrast, only
# ever changed at a known generation boundary — 4.6 for opus/sonnet — and a
# model released after this table was written inherits its family's current
# window, which is the right default rather than a wrong one.
_LONG_CONTEXT_FROM = {
    'fable': (0, 0),      # every release is 1M
    'mythos': (0, 0),     # every release is 1M
    'opus': (4, 6),       # 4.6 onward (4.5 and earlier are 200k)
    'sonnet': (4, 6),     # 4.6 onward (4.5 and earlier are 200k)
    'haiku': None,        # no 1M haiku exists
}

# Minor is optional so ``claude-opus-5`` parses as 5.0. The minor group caps at
# 3 digits WITH a trailing-digit boundary so a date suffix directly after a
# no-minor major (``claude-sonnet-4-20250514`` — a real historical id shape) is
# read as a date, not as minor 20250514. Mirrors ``model_catalog``'s regex.
_MODEL_ID_RE = re.compile(
    r'^claude-(fable|mythos|opus|sonnet|haiku)-(\d+)(?:-(\d{1,3})(?!\d))?',
)

# A bare CLI alias (``opus``) names a family with no version. The CLI resolves
# an alias to the LATEST model of that family, so the family's current window
# is the correct reading — not a guess about which specific release it hit.
_ALIAS_FAMILIES = tuple(_LONG_CONTEXT_FROM)


def context_window_tokens(model: object) -> int:
    """The model's context window in tokens, or ``0`` when unknown.

    Pass the RESOLVED model id from the stream where you have one. Bare CLI
    aliases (``opus``) are also understood, since an alias always resolves to
    the latest model of its family.

    **The ``[1m]`` marker cannot be the only signal.** The CLI accepts it on
    ``--model`` and lists it in its own model options, but it STRIPS it from
    the id it reports back on every assistant turn: a session running with a
    1M window reports plain ``claude-opus-5``. Sizing off the marker alone
    therefore fell back to 200k for every session on this account — a 97k
    conversation in a 1M window rendered as "51% left" while the CLI's own
    ``/context`` said 10% used. So the marker is honoured when present, and
    the family/version table below decides otherwise.
    """
    text = str(model or '').strip().lower()
    if not text:
        return 0
    if any(marker in text for marker in _LONG_CONTEXT_MARKERS):
        return _LONG_CONTEXT_TOKENS

    match = _MODEL_ID_RE.match(text)
    if match:
        family, major, minor = match.group(1), int(match.group(2)), match.group(3)
        return _window_for(family, (major, int(minor) if minor else 0))
    if text in _ALIAS_FAMILIES:
        # Latest of the family — the version gate is satisfied by definition.
        return _window_for(text, None)
    return 0


def _window_for(family: str, version: tuple[int, int] | None) -> int:
    """Window for a known family at a given ``(major, minor)`` release.

    ``version is None`` means "the latest release of this family" (an alias),
    which is 1M for every family that has a 1M release at all.
    """
    threshold = _LONG_CONTEXT_FROM.get(family)
    if threshold is None:
        return _SHORT_CONTEXT_TOKENS
    if version is None or version >= threshold:
        return _LONG_CONTEXT_TOKENS
    return _SHORT_CONTEXT_TOKENS


def widen_window_to_observed(limit: object, used: object) -> int:
    """Raise an assumed window that the session has already disproved.

    A turn cannot use more prompt tokens than the window holds. So if observed
    usage exceeds the limit we derived from the model id, the derivation is
    provably wrong and reporting "0% left" on it would be a false alarm — the
    backstop for a model id this module has not learned yet.

    Returns ``limit`` unchanged in the normal case (including an unknown ``0``
    window, which must stay unknown rather than being invented from usage).
    """
    limit_tokens = _positive_int(limit)
    used_tokens = _positive_int(used)
    if limit_tokens <= 0 or used_tokens <= limit_tokens:
        return limit_tokens
    if used_tokens <= _LONG_CONTEXT_TOKENS:
        return _LONG_CONTEXT_TOKENS
    return used_tokens


def _positive_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value) if value > 0 else 0


def resolved_model_of_event(raw: object) -> str:
    """The concrete model id the CLI resolved this turn to (``''`` if absent).

    The configured value may be an ALIAS (``opus``), which names a family but
    not a release. The stream reports the real id (``claude-opus-5``) on every
    assistant turn — note it arrives WITHOUT any ``[1m]`` suffix even when the
    session runs a 1M window, which is why ``context_window_tokens`` sizes off
    the family rather than the marker.
    """
    if not isinstance(raw, dict):
        return ''
    message = raw.get('message')
    if isinstance(message, dict) and message.get('model'):
        return str(message['model']).strip()
    model = raw.get('model')
    return str(model).strip() if model else ''


def prompt_tokens_from_usage(usage: object) -> int:
    """Prompt-side token total from a CLI ``usage`` payload (``0`` if absent)."""
    if not isinstance(usage, dict):
        return 0
    total = 0
    for key in _CONTEXT_USAGE_KEYS:
        value = usage.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            total += int(value)
    return max(0, total)


def usage_of_event(raw: object) -> dict:
    """The ``usage`` mapping on a CLI event, wherever the shape puts it.

    Assistant events nest it under ``message``; result events carry it at the
    top level. Returns ``{}`` for anything else so callers can treat "no
    usage on this event" as the common case it is.
    """
    if not isinstance(raw, dict):
        return {}
    message = raw.get('message')
    if isinstance(message, dict) and isinstance(message.get('usage'), dict):
        return message['usage']
    usage = raw.get('usage')
    return usage if isinstance(usage, dict) else {}
