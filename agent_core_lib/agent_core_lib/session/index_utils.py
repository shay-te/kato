"""Primitives for reading a CLI's on-disk session store.

Every agent CLI keeps its conversations as JSONL somewhere on disk, and every
transport needs the same four things to turn that into a list an operator can
recognise a conversation from: parse a line, pull the text out of a message,
clip it to something that fits a dropdown row, and match it against a search
box. The stores differ; these steps do not.

They lived twice — once in each transport's ``session/index.py`` — and had
already drifted apart in ways that showed:

- a 200-character preview came back clipped with an ellipsis from one and cut
  bare mid-word from the other, so identical rows looked different depending
  on which agent produced them;
- ``max_results=-1`` meant "unbounded" in one and "drop the last row" in the
  other, an off-by-one from ``list[:-1]``.

Neither is a big bug on its own, which is the point: forked helpers do not
announce their divergence, they just quietly stop agreeing.

This lives in ``agent_core_lib`` rather than in either transport because a
transport may not import a peer transport. The rules encoded here are also
narrow enough not to need one: nothing below knows what a Claude transcript or
a Codex rollout looks like, only what JSON and text look like.
"""

from __future__ import annotations

import json

#: How much of a transcript to read for message previews. Transcripts grow
#: without bound and reading one whole just to draw a row is waste — the first
#: user turn is near the top, and a recent one is what the operator
#: recognises.
MAX_PREVIEW_SCAN_BYTES = 256 * 1024

#: Preview length — long enough to identify a conversation, short enough to
#: keep a dropdown row on one line.
PREVIEW_LENGTH = 160


def parse_jsonl_dict_line(line: str) -> dict | None:
    """One JSONL line as a dict, or ``None``.

    The strip → ``json.loads`` → ``isinstance dict`` sequence every session
    reader repeats. Returns ``None`` for a blank line, invalid JSON, or a
    non-dict payload — never raises, because a store being written while it is
    read routinely ends mid-line and a half-written transcript must not break
    the picker.
    """
    line = str(line or '').strip()
    if not line:
        return None
    try:
        record = json.loads(line)
    except (ValueError, TypeError):
        return None
    return record if isinstance(record, dict) else None


def text_from_content(content, *, types=None, separator=' ') -> str:
    """The text of a message's ``content``, whatever shape it arrives in.

    Content is a plain string on some turns and a list of parts on others, and
    the parts are named differently per CLI (``input_text`` on a user turn,
    ``output_text`` on an assistant one, ``text`` elsewhere). ``types``
    restricts which part types are read; ``None`` reads every part that
    carries text, which is what a preview wants — the distinction is the CLI's
    own bookkeeping, not something the operator asked about.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ''
    parts = []
    for block in content:
        if isinstance(block, str):
            if block:
                parts.append(block)
            continue
        if not isinstance(block, dict):
            continue
        if types is not None and block.get('type') not in types:
            continue
        text = block.get('text')
        if isinstance(text, str) and text:
            parts.append(text)
    return separator.join(parts)


def clip_preview(text: object, length: int = PREVIEW_LENGTH) -> str:
    """``text`` collapsed to one line and clipped to ``length``.

    Ends with an ellipsis when it had to cut, so a row that was truncated does
    not read as a message that simply ended there. The result never exceeds
    ``length`` — the ellipsis replaces a character rather than being appended
    past the limit.
    """
    cleaned = ' '.join(str(text or '').split())
    if len(cleaned) <= length:
        return cleaned
    return cleaned[:max(length - 1, 0)] + '…'


def matches_query(needle: str, *fields: object) -> bool:
    """Does ``needle`` appear in any of ``fields``, case-insensitively?

    An empty needle matches everything, so a caller can pass the search box
    through without special-casing the empty state.

    Fields are joined with a newline rather than concatenated: joining them
    bare lets a match straddle the boundary between two of them, so searching
    for the tail of a path plus the head of a message would spuriously hit.
    """
    wanted = str(needle or '').strip().lower()
    if not wanted:
        return True
    return wanted in '\n'.join(str(field or '') for field in fields).lower()


def cap_results(rows: list, max_results: int) -> list:
    """The first ``max_results`` rows; a NEGATIVE cap means unbounded.

    Spelled out because the obvious ``rows[:max_results]`` silently means
    "drop the last row" for ``-1`` rather than "no limit" — which is how the
    two copies of this ended up disagreeing.
    """
    if max_results < 0:
        return list(rows)
    return list(rows[:max_results])
