"""How much context a model has, and how much of it a turn used.

Feeds the composer's context indicator. The operator watches this to decide
when to ``/compact`` — so the numbers have to be honest: an unknown window is
reported as ``0`` (render "unknown"), never as a guess, because a wrong
"93% full" pushes someone into compacting a session that had plenty of room,
and a wrong "40%" lets one hit the wall mid-task.
"""

from __future__ import annotations

# Prompt-side usage keys. Together these are the conversation as the model saw
# it this turn: fresh input plus whatever was served from / written to cache.
# ``output_tokens`` is deliberately absent — what the model wrote lands in the
# NEXT turn's prompt, so counting it here would double-count it.
_CONTEXT_USAGE_KEYS = (
    'input_tokens',
    'cache_read_input_tokens',
    'cache_creation_input_tokens',
)

# Standard window for current Claude models.
_DEFAULT_CONTEXT_TOKENS = 200_000
# The 1M-context variants the CLI marks with a ``[1m]`` suffix on the model id
# (e.g. ``claude-opus-5[1m]``), and which ``--model`` accepts the same way.
_LONG_CONTEXT_TOKENS = 1_000_000
_LONG_CONTEXT_MARKERS = ('[1m]', '-1m', ':1m')


def context_window_tokens(model: object) -> int:
    """The model's context window in tokens, or ``0`` when unknown.

    Keyed off the ``[1m]`` marker rather than a per-model table: the table
    would silently go stale on every release and start reporting a wrong
    window, which is the one failure this indicator cannot afford. An empty
    model (the CLI's own default, which the host never named) is unknown.
    """
    text = str(model or '').strip().lower()
    if not text:
        return 0
    if any(marker in text for marker in _LONG_CONTEXT_MARKERS):
        return _LONG_CONTEXT_TOKENS
    return _DEFAULT_CONTEXT_TOKENS


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
