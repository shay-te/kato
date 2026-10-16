"""What a Claude API call costs at Anthropic's published per-token rates.

The figure is an ESTIMATE of API-equivalent cost, computed from the token counts
a transcript recorded. It is not a bill: a subscription is not charged per
token, and an API key is charged for exactly what the API metered. It is the one
unit that makes a 2 GB cache re-read and a 5 MB reply comparable, because the
two are priced very differently.

Three rules make a naive "tokens x price" wrong, and each is encoded here:

* **A cache read is not 0.1x everywhere.** It is 0.1x the input rate on most
  models, 0.05x on Opus 5.5 and 0.025x on Fable 5.1, so each model carries its
  own cache-read rate instead of a shared multiplier.
* **A cache write costs 1.25x the input rate for a 5-minute entry and 2x for a
  1-hour one.** The transcript says how many of the written tokens were 1-hour.
* **Haiku 5.5 has two rate cards, chosen by the length of the PROMPT of each
  request** (input + cache read + cache write): 100K tokens or fewer, or more.
  That is a per-message property, so costing is per message, never from totals.

A model with no entry in the table is NOT priced at zero. :func:`message_cost`
returns ``None`` for it so a caller can say "no price on file" instead of
presenting a total that quietly leaves it out.

Rates are dollars per million tokens, from Anthropic's published pricing as of
:data:`PRICES_AS_OF`. Update the table and that date together.
"""

from __future__ import annotations

import re
from typing import NamedTuple

PRICES_AS_OF = '2026-10-06'

_PER_MILLION = 1_000_000

# What a cache write costs, as a multiple of the model's input rate.
_CACHE_WRITE_5M = 1.25
_CACHE_WRITE_1H = 2.0

# The three parts a cost is split into. They match the three groups the usage
# dialog shows: what the model wrote, what was sent to it new (fresh input plus
# cache writes), and the context it re-read.
COST_PARTS = ('output', 'fresh', 'cache_read')


class _Rate(NamedTuple):
    input: float
    output: float
    cache_read: float


_OPUS_STANDARD = _Rate(5.0, 25.0, 0.50)
_SONNET_5 = _Rate(2.0, 10.0, 0.20)

_RATES: dict[str, _Rate] = {
    'claude-fable-5-1': _Rate(10.0, 50.0, 0.25),
    'claude-fable-5': _Rate(10.0, 50.0, 1.00),
    'claude-opus-5-5': _Rate(4.0, 20.0, 0.20),
    'claude-opus-5': _OPUS_STANDARD,
    'claude-opus-4-8': _OPUS_STANDARD,
    'claude-opus-4-7': _OPUS_STANDARD,
    'claude-opus-4-6': _OPUS_STANDARD,
    'claude-sonnet-5-5': _SONNET_5,
    'claude-sonnet-5': _SONNET_5,
    'claude-sonnet-4-6': _Rate(3.0, 15.0, 0.30),
    'claude-haiku-5-5': _Rate(0.10, 0.50, 0.01),
    'claude-haiku-4-5': _Rate(1.0, 5.0, 0.10),
}

# Models whose rate depends on the prompt length of the request:
# model -> (longest prompt on the first card, the card for anything longer).
_LONG_PROMPT: dict[str, tuple[int, _Rate]] = {
    'claude-haiku-5-5': (100_000, _Rate(0.50, 2.50, 0.05)),
}

# A transcript names the model as the API resolved it, which can carry a
# context-window marker or a snapshot date the table does not.
_CONTEXT_MARKER = '[1m]'
_SNAPSHOT_DATE = re.compile(r'-\d{8}$')


def is_priced(model: object) -> bool:
    """Whether ``model`` has a price on file."""
    return _normalised(model) in _RATES


def message_cost(model: object, counts: dict, cache_write_1h: int = 0) -> dict | None:
    """Dollars for ONE API message, split into :data:`COST_PARTS`.

    ``counts`` holds the message's ``input`` / ``output`` / ``cache_read`` /
    ``cache_write`` tokens; ``cache_write_1h`` is how many of the written tokens
    were a 1-hour entry (the rest are 5-minute). ``None`` for a model with no
    price on file.
    """
    key = _normalised(model)
    if key not in _RATES:
        return None
    new_input = int(counts.get('input', 0) or 0)
    cache_read = int(counts.get('cache_read', 0) or 0)
    cache_write = int(counts.get('cache_write', 0) or 0)
    rate = _rate_for(key, prompt_tokens=new_input + cache_read + cache_write)
    write_1h = max(0, min(int(cache_write_1h or 0), cache_write))
    write_5m = cache_write - write_1h
    fresh_input_equivalents = (
        new_input + write_5m * _CACHE_WRITE_5M + write_1h * _CACHE_WRITE_1H
    )
    return {
        'output': int(counts.get('output', 0) or 0) * rate.output / _PER_MILLION,
        'fresh': fresh_input_equivalents * rate.input / _PER_MILLION,
        'cache_read': cache_read * rate.cache_read / _PER_MILLION,
    }


def empty_cost() -> dict:
    """A cost record with nothing priced yet."""
    return {**dict.fromkeys(COST_PARTS, 0.0), 'total': 0.0}


def add_cost(into: dict, other: dict | None) -> dict:
    """Add the cost record ``other`` to ``into`` (in place) and return ``into``."""
    for part in COST_PARTS:
        into[part] += float((other or {}).get(part, 0.0) or 0.0)
    into['total'] = sum(into[part] for part in COST_PARTS)
    return into


def rounded_cost(cost: dict) -> dict:
    """``cost`` rounded to a millionth of a dollar, so a payload carries no
    floating-point noise (the smallest message costs far more than that)."""
    parts = {part: round(cost[part], 6) for part in COST_PARTS}
    return {**parts, 'total': round(sum(parts.values()), 6)}


def _normalised(model: object) -> str:
    text = str(model or '').strip().lower()
    if text.endswith(_CONTEXT_MARKER):
        text = text[: -len(_CONTEXT_MARKER)]
    return _SNAPSHOT_DATE.sub('', text)


def _rate_for(key: str, *, prompt_tokens: int) -> _Rate:
    long_prompt = _LONG_PROMPT.get(key)
    if long_prompt is not None and prompt_tokens > long_prompt[0]:
        return long_prompt[1]
    return _RATES[key]
