"""A model id as the operator reads it: ``claude-opus-5-5[1m]`` → ``Opus 5.5 (1M context)``.

One place for the label, so the safeguard banner's fallback offer and the
review loop's model picker can never name the same model two ways.
"""

from __future__ import annotations

import re

_PINNED = re.compile(r'claude-([a-z]+)-(\d+)-(\d+)(\[1m\])?$')


def model_display_label(model: str) -> str:
    """Best effort: an id that isn't ``claude-<family>-<major>-<minor>`` (an
    alias, another vendor's model) is shown as it is."""
    text = str(model or '').strip()
    match = _PINNED.match(text)
    if not match:
        return text
    label = f'{match.group(1).capitalize()} {match.group(2)}.{match.group(3)}'
    return f'{label} (1M context)' if match.group(4) else label
