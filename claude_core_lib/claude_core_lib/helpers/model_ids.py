"""A Claude model as a person writes it, as the ``claude`` CLI takes it.

``--model`` takes an alias (``sonnet`` — the newest of the family) or a model id
(``claude-sonnet-4-6``). People write "Sonnet 4.6", "sonnet 4.6", "sonnet:4.6"
or "claude sonnet 4.6", and the CLI refused each with "There's an issue with the
selected model ... It may not exist". So every ``--model`` this lib builds goes
through ``model_flags``: a family and a version become the id, a bare family
becomes its alias, and anything else (a full id with a date, a Bedrock or Vertex
id, an older naming) is passed exactly as written.
"""

from __future__ import annotations

import re

_WRITTEN_RE = re.compile(
    r'^(?:claude[\s_:-]*)?(fable|opus|sonnet|haiku)'
    r'(?:[\s_:-]*v?(\d{1,2})(?:[.\s_-](\d{1,2}))?)?$',
    re.IGNORECASE,
)


def cli_model_id(written: object) -> str:
    """``"Sonnet 4.6"`` → ``"claude-sonnet-4-6"``; ``"Opus"`` → ``"opus"``;
    anything not a family (and version) → as written, trimmed."""
    text = str(written or '').strip()
    match = _WRITTEN_RE.match(text)
    if not match:
        return text
    family, major, minor = match.group(1).lower(), match.group(2), match.group(3)
    if not major:
        return family
    return f'claude-{family}-{int(major)}' + (f'-{int(minor)}' if minor else '')


def model_flags(model: object) -> list[str]:
    """``['--model', <id>]``, or ``[]`` when no model was asked for."""
    model_id = cli_model_id(model)
    return ['--model', model_id] if model_id else []
