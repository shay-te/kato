"""What a task kato takes by itself is told, in its system prompt.

Without it the agent waits for the operator to say the work is done before it
prints the done token (the wait-planning prompt asks exactly that), so a task
on autopilot or auto-wait would never finish by itself. The two differ only in
what kato does next: autopilot presses Done; auto-wait reviews and waits.
"""

from __future__ import annotations

from kato_core_lib.data_layers.data.sentinels import KATO_TASK_DONE_SENTINEL
from kato_core_lib.helpers.automation_switches_utils import run_tests_enabled
from kato_core_lib.helpers.autopilot_store import AUTO_WAIT, AUTOPILOT, automation_level

_AFTER_IT = {
    AUTOPILOT: (
        '## Autopilot — this task finishes by itself',
        'The operator put this task on autopilot.',
        'Kato then reviews the change if a review loop was asked for, and presses Done itself '
        '(push, pull request, ticket).',
    ),
    AUTO_WAIT: (
        '## Auto-wait — this task finishes by itself, then waits for the operator',
        'The operator put this task on auto-wait.',
        'Kato then reviews the change in a review loop and leaves the task for the operator: '
        'nothing is pushed and no pull request is opened until they press Done.',
    ),
}


def _guidance(level: str, finished_when: str) -> str:
    heading, opening, then = _AFTER_IT[level]
    return '\n'.join([
        heading,
        f'{opening} Once you are implementing (after any plan they must approve first), carry '
        'the task through to the end without waiting for them to say it is done: when '
        f'{finished_when}, end your final reply with this exact token on its own line: '
        f'{KATO_TASK_DONE_SENTINEL}',
        f'{then} This replaces any instruction to wait for the operator before printing the '
        'token. Never print it while work is left, nor in a review fix turn.',
    ])


AUTOPILOT_GUIDANCE = _guidance(AUTOPILOT, 'it is implemented and its tests pass')
# "Run tests" off (Settings → Automation): "its tests pass" would send the
# agent to run them — exactly what the operator turned off.
AUTOPILOT_GUIDANCE_NO_TESTS = _guidance(AUTOPILOT, 'it is implemented')
AUTO_WAIT_GUIDANCE = _guidance(AUTO_WAIT, 'it is implemented and its tests pass')
AUTO_WAIT_GUIDANCE_NO_TESTS = _guidance(AUTO_WAIT, 'it is implemented')

_SECTIONS = {
    (AUTOPILOT, True): AUTOPILOT_GUIDANCE,
    (AUTOPILOT, False): AUTOPILOT_GUIDANCE_NO_TESTS,
    (AUTO_WAIT, True): AUTO_WAIT_GUIDANCE,
    (AUTO_WAIT, False): AUTO_WAIT_GUIDANCE_NO_TESTS,
}


def with_autopilot_guidance(task_id: object, guidance: str) -> str:
    """``guidance`` plus the section for the task's automation level, if any."""
    level = automation_level(task_id)
    if not level:
        return guidance
    section = _SECTIONS[(level, run_tests_enabled())]
    base = str(guidance or '').rstrip()
    return f'{base}\n\n{section}' if base else section
