"""Stop the agent re-reading a file it already has in context.

Measured on two real task transcripts (4,565 tool calls): 704 ``Read`` calls
covered only 193 distinct files. One file was read 54 times. Splitting every
re-read by whether it was justified:

  * 27.5% first read of a file
  * 25.8% re-read after the agent EDITED it        (legitimate)
  * 22.9% re-read after a compaction               (necessary — it was gone)
  * **23.9% re-read of an unchanged file with no compaction since** — the
    content was still sitting in the context window. ~173k tokens, or ~13%
    of everything the tools put into context, spent re-sending bytes the
    model already had.

That last slice is the only one this module touches. It is pure waste with no
judgment call attached: the file has not changed, the agent has not edited it,
and nothing dropped it from the conversation.

**The mechanism.** A ``PreToolUse`` hook on ``Read``. On each call we compare
the file's (mtime, size) against what we served last time in this session. If
they match and the previous serve was recent, we return a ``deny`` decision
whose reason tells the agent it already has the file — the model reads that
reason instead of a second copy of the file.

**Three deliberate escape hatches**, because starving the agent of context it
genuinely lost is far worse than the tokens this saves:

  * A ranged read (``offset``/``limit``) ALWAYS passes. That is the documented
    way for the agent to force a re-read, and the deny reason says so.
  * The recency window. Anything served longer ago than the window passes,
    because a compaction may have dropped it and this hook cannot see
    compaction boundaries.
  * Any failure — unreadable state, malformed payload, missing file — passes.
    Fail OPEN, always.

Generic by design (no product names): the state directory arrives via
``AGENT_READ_DEDUPE_STATE_DIR`` and the window via
``AGENT_READ_DEDUPE_WINDOW_SECONDS``.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

STATE_DIR_ENV = 'AGENT_READ_DEDUPE_STATE_DIR'
WINDOW_ENV = 'AGENT_READ_DEDUPE_WINDOW_SECONDS'

# How long a served file stays "the agent still has this". Measured: 38% of
# the wasteful re-reads recur within 5 turns and 49% within 20, while the
# compactions that would legitimately drop a file happened 16 times across
# 9,819 turns (they fire near the 1M-token mark). A window of minutes is
# therefore far shorter than any plausible gap to a compaction, and still
# catches the tight read-read-read loops that hold most of the waste.
DEFAULT_WINDOW_SECONDS = 900.0

# Cap the per-session state so a very long session can't grow it without
# bound. Oldest entries are dropped first; dropping one only costs a
# re-read that would have happened anyway before this hook existed.
_MAX_TRACKED_FILES = 512


def _window_seconds() -> float:
    try:
        value = float(os.environ.get(WINDOW_ENV, '') or DEFAULT_WINDOW_SECONDS)
    except (TypeError, ValueError):
        return DEFAULT_WINDOW_SECONDS
    return value if value > 0 else DEFAULT_WINDOW_SECONDS


def _state_path(session_id: str) -> Path | None:
    directory = str(os.environ.get(STATE_DIR_ENV, '') or '').strip()
    if not directory:
        return None
    safe = ''.join(c for c in str(session_id or '') if c.isalnum() or c in '-_')
    if not safe:
        return None
    return Path(directory) / f'read-dedupe-{safe}.json'


def _load(path: Path | None) -> dict:
    if path is None:
        return {}
    try:
        with path.open('r', encoding='utf-8') as handle:
            data = json.load(handle)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _save(path: Path | None, state: dict) -> None:
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix('.tmp')
        with tmp.open('w', encoding='utf-8') as handle:
            json.dump(state, handle)
        os.replace(tmp, path)
    except Exception:
        # Losing the state costs a re-read, which is the status quo.
        pass


def _stat_signature(file_path: str) -> tuple[int, int] | None:
    try:
        stat = os.stat(file_path)
    except OSError:
        return None
    return int(stat.st_mtime_ns), int(stat.st_size)


def _prune(state: dict, now: float, window: float) -> dict:
    fresh = {
        path: entry for path, entry in state.items()
        if isinstance(entry, dict) and (now - float(entry.get('at', 0) or 0)) < window
    }
    if len(fresh) <= _MAX_TRACKED_FILES:
        return fresh
    newest = sorted(
        fresh.items(), key=lambda kv: float(kv[1].get('at', 0) or 0), reverse=True,
    )[:_MAX_TRACKED_FILES]
    return dict(newest)


def decide(payload: dict, *, now: float | None = None) -> dict | None:
    """The whole decision, as a pure function of the hook payload.

    Returns the hook-output dict that blocks the read, or ``None`` to let it
    through. Every uncertain path returns ``None``.
    """
    if not isinstance(payload, dict):
        return None
    if str(payload.get('tool_name', '')) != 'Read':
        return None
    tool_input = payload.get('tool_input')
    if not isinstance(tool_input, dict):
        return None
    # A ranged read is the agent asking for a specific slice — always serve
    # it, and it doubles as the escape hatch from this hook.
    if tool_input.get('offset') or tool_input.get('limit'):
        return None
    file_path = str(tool_input.get('file_path', '') or '')
    if not file_path:
        return None
    signature = _stat_signature(file_path)
    if signature is None:
        return None

    moment = time.time() if now is None else now
    window = _window_seconds()
    path = _state_path(str(payload.get('session_id', '') or ''))
    state = _prune(_load(path), moment, window)

    previous = state.get(file_path)
    served_before = (
        isinstance(previous, dict)
        and int(previous.get('mtime_ns', -1) or -1) == signature[0]
        and int(previous.get('size', -1) or -1) == signature[1]
    )
    if served_before:
        # Do NOT refresh the timestamp: the window is measured from when the
        # agent last actually RECEIVED the content, not from the last time it
        # asked. Otherwise a file asked for repeatedly would stay suppressed
        # forever, long past any compaction that dropped it.
        return _deny(file_path, moment - float(previous.get('at', 0) or 0))

    state[file_path] = {
        'mtime_ns': signature[0], 'size': signature[1], 'at': moment,
    }
    # Cap AFTER inserting, or the file just served would push the state one
    # entry over the limit on every call.
    _save(path, _prune(state, moment, window))
    return None


def _deny(file_path: str, age_seconds: float) -> dict:
    minutes = max(1, int(age_seconds // 60))
    reason = (
        f'You already read {file_path} about {minutes} minute(s) ago and it '
        f'has not changed since — its contents are still above in this '
        f'conversation. Scroll back rather than re-reading it. If you no '
        f'longer have it, read it again with an explicit offset/limit range, '
        f'which is always served.'
    )
    return {
        'hookSpecificOutput': {
            'hookEventName': 'PreToolUse',
            'permissionDecision': 'deny',
            'permissionDecisionReason': reason,
        },
    }


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``python -m ...helpers.read_dedupe``.

    Always exits 0: a hook that errors must not break the agent's read.
    """
    del argv
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    try:
        decision = decide(payload)
    except Exception:
        return 0
    if decision:
        json.dump(decision, sys.stdout)
    return 0


if __name__ == '__main__':  # pragma: no cover - process entry point
    raise SystemExit(main())
