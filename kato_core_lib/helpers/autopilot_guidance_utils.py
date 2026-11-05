"""What a task on autopilot is told, in its system prompt.

Without it the agent waits for the operator to say the work is done before it
prints the done token (the wait-planning prompt asks exactly that), so a task
on autopilot would never finish by itself.
"""

from __future__ import annotations

from kato_core_lib.data_layers.data.sentinels import KATO_TASK_DONE_SENTINEL
from kato_core_lib.helpers.autopilot_store import is_autopilot

AUTOPILOT_GUIDANCE = '\n'.join([
    '## Autopilot — this task finishes by itself',
    'The operator put this task on autopilot. Once you are implementing (after any plan they '
    'must approve first), carry the task through to the end without waiting for them to say '
    'it is done: when it is implemented and its tests pass, end your final reply with this '
    f'exact token on its own line: {KATO_TASK_DONE_SENTINEL}',
    'Kato then reviews the change if a review loop was asked for, and presses Done itself '
    '(push, pull request, ticket). This replaces any instruction to wait for the operator before '
    'printing the token. Never print it while work is left, nor in a review fix turn.',
])


def with_autopilot_guidance(task_id: object, guidance: str) -> str:
    """``guidance`` plus the autopilot section, for a task on autopilot."""
    if not is_autopilot(task_id):
        return guidance
    base = str(guidance or '').rstrip()
    return f'{base}\n\n{AUTOPILOT_GUIDANCE}' if base else AUTOPILOT_GUIDANCE
