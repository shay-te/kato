"""Hook: nothing else runs until the lessons file has been read — ALL of it.

The lessons file used to be pasted into the appended system prompt, which is
what made the agent read it — and what pushed the Windows spawn command line
past its 32,767-character limit until the CLI would not start at all. The
prompt now names the path instead.

Prompt wording alone is not enforcement. This hook is: every tool call is
denied, with a reason naming the file, until the file has been read in this
session. ``Read`` itself is never blocked, so the agent always has a way to
satisfy the gate.

"Read" means every line. The gate used to open on the first ``Read`` call
that NAMED the file — before the call had run. So it opened on a read that
then failed (a file over the tool's size limit fails outright unless it is
paged), and on the first page of a file that needs five. A lessons file big
enough to need paging is exactly the one whose later pages hold most of the
rules. Two events now feed one record:

* ``PostToolUse`` for ``Read`` reports what a read actually RETURNED —
  ``startLine``, ``numLines``, ``totalLines`` — and that range is added to the
  session's coverage. A failed read reports nothing, so it covers nothing.
* ``PreToolUse`` for everything else checks that the coverage reaches from
  line 1 to the last line, and otherwise denies — naming the next line to read
  from.

Covers ``--resume`` because the record is keyed on the CLI's own session id
and stored on disk, the same mechanism ``read_dedupe`` uses: a resumed session
that already read the file stays unblocked, and one that never did is still
gated.

Fail-OPEN in every failure path. A hook that errors, cannot find or write its
state, or never hears back about a read must not be able to wedge an agent
into denying every tool forever — a missed gate costs one unread file; a stuck
gate costs the task. See ``_gives_up``.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

#: Directory for the per-session record. Its OWN variable rather than reusing
#: ``read_dedupe``'s: that hook is opt-in and can be switched off, and a gate
#: that silently stopped recording because an unrelated feature was disabled
#: would deny every tool for the rest of the session.
STATE_DIR_ENV = 'AGENT_LESSONS_GATE_STATE_DIR'

#: The file the agent must read. Set by the caller when it builds the
#: ``--settings`` payload; without it the gate does nothing at all.
LESSONS_PATH_ENV = 'AGENT_LESSONS_PATH'

#: Tools that are never gated. ``Read`` is how the gate is satisfied, so
#: blocking it would be a deadlock. The rest are the agent's ways of asking a
#: human something or ending cleanly — none of them touch the codebase, and
#: blocking them turns the gate into a hang rather than a nudge.
_ALWAYS_ALLOWED = frozenset({
    'Read', 'AskUserQuestion', 'ExitPlanMode', 'TodoWrite',
})

#: The event that carries a finished read's result.
_POST_TOOL_USE = 'PostToolUse'

#: Denials after which the gate stands down although no read was ever reported
#: back. The agent asked to read the file and was told nothing about the
#: outcome: either the result event is not wired on this CLI, or every attempt
#: failed. Neither is something more denying will fix.
_UNREPORTED_DENIALS = 3

#: Denials after which the gate stands down whatever the record says — a file
#: the tool cannot return in full (a single line over its size limit, say)
#: would otherwise hold the session for good.
_MAX_DENIALS = 8

#: A page size that fits the read tool's limit on ordinary prose. Named in the
#: denial so the agent's next call is one that works, not another refusal.
_SUGGESTED_PAGE_LINES = 400


def _state_path(session_id: str) -> Path | None:
    directory = str(os.environ.get(STATE_DIR_ENV, '') or '').strip()
    if not directory:
        return None
    safe = ''.join(c for c in str(session_id or '') if c.isalnum() or c in '-_')
    if not safe:
        return None
    return Path(directory) / f'lessons-gate-{safe}.json'


def _load(path: Path) -> dict:
    try:
        with open(path, encoding='utf-8') as handle:
            state = json.load(handle)
    except Exception:
        return {}
    return state if isinstance(state, dict) else {}


def _save(path: Path, state: dict) -> bool:
    """Persist ``state``. False when it could not be written."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as handle:
            json.dump(state, handle)
        return True
    except Exception:
        return False


def _merged(ranges: list) -> list[list[int]]:
    """``ranges`` as sorted, non-overlapping, inclusive ``[first, last]`` pairs."""
    merged: list[list[int]] = []
    for first, last in sorted(
        [int(r[0]), int(r[1])] for r in ranges
        if isinstance(r, (list, tuple)) and len(r) == 2
    ):
        if merged and first <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], last)
        else:
            merged.append([first, last])
    return merged


def _first_unread_line(state: dict) -> int:
    """The first line no read has returned yet; 0 when every line has been."""
    total = int(state.get('total') or 0)
    if total <= 0:
        return 1
    expected = 1
    for first, last in _merged(state.get('ranges') or []):
        if first > expected:
            break
        expected = max(expected, last + 1)
    return 0 if expected > total else expected


def _record_read(state: dict, tool_response: object) -> None:
    """Add what a finished read returned to the session's coverage."""
    file_info = tool_response.get('file') if isinstance(tool_response, dict) else None
    if not isinstance(file_info, dict):
        return
    try:
        start = int(file_info.get('startLine') or 1)
        count = int(file_info.get('numLines') or 0)
        total = int(file_info.get('totalLines') or 0)
    except (TypeError, ValueError):
        return
    if count <= 0:
        return
    state['ranges'] = _merged(
        list(state.get('ranges') or []) + [[start, start + count - 1]],
    )
    # The latest read is the authority on the file's length: it can change
    # while it is being paged through, and it is the file as it IS that has to
    # be covered.
    state['total'] = max(total, start + count - 1)
    if _first_unread_line(state) == 0:
        state['read'] = True


def _gives_up(state: dict) -> bool:
    """True when denying again would only wedge the session."""
    denials = int(state.get('denials') or 0)
    if denials >= _MAX_DENIALS:
        return True
    never_reported = not state.get('ranges')
    return (
        never_reported
        and int(state.get('attempts') or 0) > 0
        and denials >= _UNREPORTED_DENIALS
    )


def _denial_reason(lessons_path: str, state: dict) -> str:
    next_line = _first_unread_line(state)
    total = int(state.get('total') or 0)
    if total > 0 and next_line > 1:
        return (
            f'You have read only part of the lessons file: {lessons_path}\n'
            f'Lines 1-{next_line - 1} of {total} are read. Every tool except '
            f'Read is blocked until the WHOLE file has been read in this '
            f'session. Continue with the Read tool on that exact path, '
            f'offset={next_line} (limit={_SUGGESTED_PAGE_LINES} keeps a page '
            f'inside the read limit), and repeat until you reach line {total}.'
        )
    return (
        f'Read the lessons file first: {lessons_path}\n'
        f'It holds rules extracted from real mistakes made on earlier '
        f'tasks in this codebase, and every tool except Read is '
        f'blocked until you have read ALL of it in this session. Use the '
        f'Read tool on that exact path. If it is too large to return in one '
        f'call, page through it with offset and limit '
        f'(limit={_SUGGESTED_PAGE_LINES}) from line 1 to the end.'
    )


def _same_file(left: str, right: str) -> bool:
    """Path equality that survives ``~``, relative segments and case.

    The agent echoes back whatever path form it was given; the gate must not
    hinge on it matching the caller's spelling character for character.
    """
    if not left or not right:
        return False
    try:
        return os.path.normcase(os.path.realpath(os.path.expanduser(left))) == \
            os.path.normcase(os.path.realpath(os.path.expanduser(right)))
    except Exception:
        return os.path.normcase(left) == os.path.normcase(right)


def decide(payload: dict) -> dict | None:
    """``None`` to allow, or a PreToolUse deny decision."""
    lessons_path = str(os.environ.get(LESSONS_PATH_ENV, '') or '').strip()
    # No lessons file configured (or it was blank, so no directive was
    # injected either) — the gate is inert.
    if not lessons_path:
        return None
    tool_name = str(payload.get('tool_name', '') or '')
    path = _state_path(str(payload.get('session_id', '') or ''))

    if tool_name == 'Read':
        tool_input = payload.get('tool_input')
        target = ''
        if isinstance(tool_input, dict):
            target = str(tool_input.get('file_path', '') or '')
        if path is not None and _same_file(target, lessons_path):
            state = _load(path)
            if payload.get('hook_event_name') == _POST_TOOL_USE:
                _record_read(state, payload.get('tool_response'))
            else:
                # The request, before it has run. It proves nothing about what
                # was read; it is counted only so a session whose reads are
                # never reported back can be told apart from one that never
                # tried.
                state['attempts'] = int(state.get('attempts') or 0) + 1
            _save(path, state)
        return None

    if tool_name in _ALWAYS_ALLOWED:
        return None
    # No state directory means the record can never be written, so gating
    # here would deny every tool for the whole session. Fail open.
    if path is None:
        return None
    state = _load(path)
    if state.get('read'):
        return None
    if _gives_up(state):
        state['read'] = True
        _save(path, state)
        return None
    state['denials'] = int(state.get('denials') or 0) + 1
    # A denial that cannot be counted can never run out, and a record that
    # cannot be written can never show the file as read. Stand down.
    if not _save(path, state):
        return None

    return {
        'hookSpecificOutput': {
            'hookEventName': 'PreToolUse',
            'permissionDecision': 'deny',
            'permissionDecisionReason': _denial_reason(lessons_path, state),
        },
    }


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``python -m ...helpers.lessons_gate``.

    Always exits 0: a hook that errors must not break the agent.
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
