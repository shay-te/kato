"""The prompt that asks an agent to split a plan into steps.

The plan text comes from a file an agent wrote, so the host wraps it as
untrusted content before it reaches this prompt; the rules below are the
lib's, and the host adds its own product guidance. The reply must end with one
``<task-split>`` block, read strictly by :mod:`split`.

Two kinds of request: split a plan (``build_propose_prompt``), or map a split
the operator already wrote as files (``build_files_prompt``) — one step per
file, the file's text kept as its brief, so the agent only names each step and
works out what it waits for and where it works.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from plan_queue_core_lib.plan_queue_core_lib.graph import MAX_BRIEF_CHARS, MAX_STEPS, StepDraft

SPLIT_OPEN = '<task-split>'
SPLIT_CLOSE = '</task-split>'

_FORMAT_EXAMPLE = {
    'steps': [
        {
            'id': 'api',
            'title': 'Add the endpoint',
            'brief': 'What to change, where, and how to know it is done.',
            'depends_on': [],
            'repositories': ['<repository>'],
        },
        {
            'id': 'ui',
            'title': 'Use the endpoint in the page',
            'brief': '...',
            'depends_on': ['api'],
            'repositories': ['<repository>'],
        },
    ],
    'notes': 'Anything the operator should know about this split.',
}

_FILES_EXAMPLE = {
    'steps': [
        {
            'id': 't01',
            'title': 'New sign-in realm',
            'file': '<folder>/01-realm.md',
            'depends_on': [],
            'repositories': ['<repository>'],
        },
        {
            'id': 't02',
            'title': 'Sign-in page',
            'file': '<folder>/02-login.md',
            'depends_on': ['t01'],
            'repositories': ['<repository>'],
        },
    ],
    'notes': 'Anything the operator should know — e.g. a dependency that names no file here.',
}


def build_propose_prompt(
    *,
    plan_title: str,
    wrapped_plan: str,
    repositories: Sequence[str],
    guidance: str = '',
    previous_steps: Sequence[StepDraft] = (),
    feedback: str = '',
    problem: str = '',
) -> str:
    """The proposer's instructions.

    ``wrapped_plan`` is the plan text already framed as untrusted by the host.
    ``previous_steps`` + ``feedback`` turn this into a revision of an earlier
    split; ``problem`` is why the last reply could not be used, so the next
    one can fix exactly that.
    """
    parts = [
        f'Split the approved plan "{plan_title}" into small, separately reviewable '
        'tasks. Each task becomes its own change, reviewed and merged on its own. '
        'Read the code as much as you need to split well; do not change anything.',
        '',
        'The plan:',
        wrapped_plan,
        '',
        'Repositories a task may work in: ' + ', '.join(repositories) + '.',
        '',
        'Rules:',
        f'- At most {MAX_STEPS} tasks. Prefer fewer, meaningful tasks over many tiny ones.',
        '- A task depends on another ONLY if it needs that task\'s change already merged. '
        'Tasks with no dependency between them may run at the same time.',
        '- Every brief must stand on its own: whoever does the task sees the brief, not '
        f'the plan. Say what to change, where, and how to know it is done (at most '
        f'{MAX_BRIEF_CHARS} characters).',
        '- Ids: 1-24 lowercase letters, digits, "-" or "_", starting with a letter.',
        '- "repositories" lists only repositories from the list above; leave it empty '
        'to mean all of them.',
    ]
    previous = [_draft_dict(draft) for draft in previous_steps]
    return '\n'.join(parts + _closing(guidance, previous, feedback, problem, _FORMAT_EXAMPLE))


def build_files_prompt(
    *,
    plan_title: str,
    wrapped_files: Sequence[tuple[str, str]],
    wrapped_overview: str,
    repositories: Sequence[str],
    guidance: str = '',
    previous_steps: Sequence[tuple[StepDraft, str]] = (),
    feedback: str = '',
    problem: str = '',
) -> str:
    """The instructions for mapping a split the operator already wrote.

    ``wrapped_files`` is ``(path, text framed as untrusted)`` per file, in
    order; ``wrapped_overview`` the folder's overview (framed), or ''.
    ``previous_steps`` pairs each earlier step with the file it was made from.
    """
    parts = [
        f'The work "{plan_title}" is already split into tasks by the operator: one file '
        'per task. Do NOT split, merge, reorder or rewrite them. For each file, in the '
        'order given, make exactly one task. Read the code as much as you need; do not '
        'change anything.',
    ]
    if wrapped_overview:
        parts += ['', 'The overview of the split:', wrapped_overview]
    for path, wrapped in wrapped_files:
        parts += ['', f'File {path}:', wrapped]
    parts += [
        '',
        'Repositories a task may work in: ' + ', '.join(repositories) + '.',
        '',
        'For each file give:',
        '- "file": its path exactly as written above. Every file once, no other files.',
        '- "id": 1-24 lowercase letters, digits, "-" or "_", starting with a letter '
        '(e.g. "t03" for a file numbered 03).',
        '- "title": a short title, from the file\'s own heading.',
        '- "depends_on": the ids of the tasks it needs merged first, as the file (and the '
        'overview) says. Only tasks in this list; if it names something that is not one of '
        'these files, leave it out and say so in "notes".',
        '- "repositories": the repositories from the list above whose code it changes '
        '(from the paths it names); empty means all of them.',
        '- No "brief": the file\'s text IS the brief, exactly as written.',
    ]
    previous = [_draft_dict(draft, file=path) for draft, path in previous_steps]
    return '\n'.join(parts + _closing(guidance, previous, feedback, problem, _FILES_EXAMPLE))


def _closing(guidance: str, previous: list[dict], feedback: str, problem: str, example: dict) -> list[str]:
    """What every request ends with: the host's guidance, the earlier split
    and the operator's feedback (a revision), why the last reply failed (a
    repair), and the exact shape of the answer."""
    parts = []
    if guidance.strip():
        parts += ['', guidance.strip()]
    if previous:
        parts += ['', 'Your previous split was:', json.dumps({'steps': previous}, indent=2)]
    if feedback.strip():
        parts += ['', 'The operator asked for these changes:', feedback.strip()]
    if problem.strip():
        parts += ['', 'Your last reply could not be used: ' + problem.strip(), 'Fix exactly that.']
    return parts + [
        '',
        f'End your reply with exactly one {SPLIT_OPEN} block holding valid JSON in this '
        'shape, and nothing after it:',
        SPLIT_OPEN,
        json.dumps(example, indent=2),
        SPLIT_CLOSE,
    ]


def _draft_dict(draft: StepDraft, *, file: str = '') -> dict:
    entry = {'id': draft.id, 'title': draft.title}
    if file:
        entry['file'] = file  # its brief is that file, not repeated here
    else:
        entry['brief'] = draft.brief
    entry['depends_on'] = list(draft.depends_on)
    entry['repositories'] = list(draft.repositories)
    return entry
