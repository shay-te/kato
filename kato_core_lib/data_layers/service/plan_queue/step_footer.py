"""The footer a plan's sub-task carries in its description.

It says where the ticket belongs, what it waits for and where it works — so a
person opening the ticket knows, and another kato that picks it up can find its
plan (the pickup gate) and its repositories (repository resolution) without
tags, which a tracker may keep private to one user. The last line is the
marker a retried create looks for.

    Kato plan queue
    Part of PROJ-1 · task 2 of 4
    Waits for: PROJ-2 (merged and done before this starts)
    Repositories: demo-shop-be
    plan-step:7f3a…c2/ui
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from plan_queue_core_lib.plan_queue_core_lib.ports import LaunchRequest

FOOTER_HEADING = 'Kato plan queue'
MAX_SUMMARY_CHARS = 250
_PART_OF = re.compile(r'^Part of (?P<source>\S+) · task \d+ of \d+$')
_REPOSITORIES = re.compile(r'^Repositories: (?P<repos>.+)$')
_MARKER = re.compile(r'^plan-step:(?P<plan>[0-9a-f]{32})/(?P<step>[a-z][a-z0-9_-]{0,23})$')
_EVERY_REPOSITORY = 'all of the plan\'s'


@dataclass(frozen=True)
class StepFooter(object):
    source_task_id: str
    plan_id: str
    step_id: str
    repositories: tuple[str, ...] = ()


def subtask_summary(request: LaunchRequest) -> str:
    """``Subtask of PROJ-1: Add the endpoint``."""
    return f'Subtask of {request.source_task_id}: {request.title}'[:MAX_SUMMARY_CHARS]


def subtask_description(request: LaunchRequest, repositories: Sequence[str]) -> str:
    """The step's brief (as the queue composed it), then the footer."""
    waits = ', '.join(request.waits_for) or 'nothing'
    return '\n'.join([
        request.description.rstrip(),
        '',
        FOOTER_HEADING,
        f'Part of {request.source_task_id} · task {request.index} of {request.count}',
        f'Waits for: {waits} (merged and done before this starts)',
        f'Repositories: {", ".join(repositories) or _EVERY_REPOSITORY}',
        request.marker,
    ])


def parse_footer(text: object) -> StepFooter | None:
    """The footer in a ticket's description, or None when it has none."""
    source = plan_id = step_id = ''
    repositories: tuple[str, ...] = ()
    for line in str(text or '').splitlines():
        line = line.strip()
        part_of = _PART_OF.match(line)
        if part_of:
            source = part_of.group('source')
        listed = _REPOSITORIES.match(line)
        if listed and listed.group('repos').strip() != _EVERY_REPOSITORY:
            repositories = tuple(item.strip() for item in listed.group('repos').split(',') if item.strip())
        marker = _MARKER.match(line)
        if marker:
            plan_id, step_id = marker.group('plan'), marker.group('step')
    if not (source and plan_id):
        return None
    return StepFooter(source, plan_id, step_id, repositories)
