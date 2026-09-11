"""``PreToolUse`` hook: nothing else runs until the lessons file is read.

The lessons file used to be pasted into the appended system prompt, which is
what made the agent read it — and what pushed the Windows spawn command line
past its 32,767-character limit until the CLI would not start at all. The
prompt now names the path instead.

Prompt wording alone is not enforcement. This hook is: every tool call is
denied, with a reason naming the file, until a ``Read`` of that file has been
seen in this session. The ``Read`` itself is never blocked, so the agent
always has a way to satisfy the gate.

Covers ``--resume`` because the marker is keyed on the CLI's own session id
and stored on disk, the same mechanism ``read_dedupe`` uses: a resumed session
that already read the file stays unblocked, and one that never did is still
gated.

Fail-OPEN in every failure path. A hook that errors, or that cannot find its
state directory, must never be able to wedge an agent into denying every tool
forever — a missed gate costs one unread file; a stuck gate costs the task.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

#: Directory for the per-session marker. Its OWN variable rather than reusing
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


def _state_path(session_id: str) -> Path | None:
    directory = str(os.environ.get(STATE_DIR_ENV, '') or '').strip()
    if not directory:
        return None
    safe = ''.join(c for c in str(session_id or '') if c.isalnum() or c in '-_')
    if not safe:
        return None
    return Path(directory) / f'lessons-gate-{safe}.json'


def _already_read(path: Path | None) -> bool:
    if path is None:
        return False
    try:
        with open(path, encoding='utf-8') as handle:
            return bool(json.load(handle).get('read'))
    except Exception:
        return False


def _mark_read(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as handle:
            json.dump({'read': True}, handle)
    except Exception:
        # Best effort. Failing to record it means the agent is asked to read
        # the file again — annoying, never fatal.
        pass


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
    state = _state_path(str(payload.get('session_id', '') or ''))

    if tool_name == 'Read':
        tool_input = payload.get('tool_input')
        target = ''
        if isinstance(tool_input, dict):
            target = str(tool_input.get('file_path', '') or '')
        if _same_file(target, lessons_path):
            _mark_read(state)
        return None

    if tool_name in _ALWAYS_ALLOWED:
        return None
    if _already_read(state):
        return None
    # No state directory means the marker can never be written, so gating
    # here would deny every tool for the whole session. Fail open.
    if state is None:
        return None

    return {
        'hookSpecificOutput': {
            'hookEventName': 'PreToolUse',
            'permissionDecision': 'deny',
            'permissionDecisionReason': (
                f'Read the lessons file first: {lessons_path}\n'
                f'It holds rules extracted from real mistakes made on earlier '
                f'tasks in this codebase, and every tool except Read is '
                f'blocked until you have read it in this session. Use the '
                f'Read tool on that exact path, then continue.'
            ),
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
