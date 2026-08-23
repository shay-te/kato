"""What a chat cost on its FIRST turn — recovered from its own transcript.

The cost indicator compares a chat's current per-turn cost against its floor:
what a fresh chat would cost (system prompt + project instructions + injected
docs). For a chat that starts AFTER the indicator exists, the floor is simply
the first reading anyone measures.

For a chat already in progress there is no such reading — and adopting its
CURRENT size as the floor would report the most expensive chat on the machine
as 1.0x, a green light on exactly the conversation that needs restarting. So
rather than guessing, read the floor out of the transcript the CLI has been
writing all along: ``~/.claude/projects/<encoded-cwd>/<session-id>.jsonl``,
whose FIRST assistant turn carries the usage numbers for a context that was,
at that moment, brand new.

Head-capped: the first assistant turn is at the top of an append-ordered file,
so this reads a few dozen lines, never the whole log (they reach hundreds of
megabytes on a long session).
"""

from __future__ import annotations

import json
from pathlib import Path

from claude_core_lib.claude_core_lib.session.index import claude_project_dir_for_cwd

# How far into the transcript to look for the first assistant turn. The
# opening lines are metadata plus the first user message; a turn that has not
# appeared within this many lines is not worth hunting for — the caller simply
# gets no floor, and the indicator shows nothing rather than something wrong.
_MAX_LINES = 200


def _usage_total(usage: dict) -> int:
    """Tokens the model READ for one turn — the size of the context then.

    Cache reads count: they are the same prompt, billed at a different rate.
    Output tokens do not — they are what the turn produced, not what it cost
    to send.
    """
    total = 0
    for key in (
        'input_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens',
    ):
        try:
            total += max(0, int(usage.get(key, 0) or 0))
        except (TypeError, ValueError):
            continue
    return total


def first_turn_tokens(transcript: Path) -> int:
    """Context size at the transcript's first assistant turn (0 if unknown)."""
    try:
        with transcript.open('r', encoding='utf-8', errors='replace') as handle:
            for index, line in enumerate(handle):
                if index >= _MAX_LINES:
                    return 0
                try:
                    record = json.loads(line)
                except Exception:
                    continue
                if not isinstance(record, dict):
                    continue
                message = record.get('message')
                if not isinstance(message, dict):
                    continue
                usage = message.get('usage')
                if not isinstance(usage, dict):
                    continue
                total = _usage_total(usage)
                if total > 0:
                    return total
    except OSError:
        return 0
    return 0


def chat_floor_tokens(session_id: str, cwd: str) -> int:
    """The floor for an existing chat, or 0 when it cannot be established.

    0 is a real answer — the caller must show no cost reading rather than
    inventing one.
    """
    normalized_id = str(session_id or '').strip()
    normalized_cwd = str(cwd or '').strip()
    if not normalized_id or not normalized_cwd:
        return 0
    try:
        transcript = claude_project_dir_for_cwd(normalized_cwd) / f'{normalized_id}.jsonl'
        if not transcript.is_file():
            return 0
    except OSError:
        return 0
    return first_turn_tokens(transcript)
