"""Check a proposed split and order its steps.

One validator for every split, whether an agent proposed it or the operator
edited it: the same rules, the same wording, so what the review screen accepts
is exactly what the queue can run. A split that breaks a rule is refused with a
reason the operator can act on — it is never repaired by guessing.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

MAX_STEPS = 25
MAX_TITLE_CHARS = 200
MAX_BRIEF_CHARS = 8000
_STEP_ID = re.compile(r'^[a-z][a-z0-9_-]{0,23}$')


class SplitValidationError(ValueError):
    """The split breaks a rule; the message says which, for the operator."""


@dataclass(frozen=True)
class StepDraft(object):
    """One step as proposed or edited, before it becomes part of a plan."""

    id: str
    title: str
    brief: str
    depends_on: tuple[str, ...] = ()
    repositories: tuple[str, ...] = ()


def validate_split(
    drafts: Sequence[StepDraft], *, allowed_repositories: Iterable[str],
) -> list[StepDraft]:
    """The drafts, checked and normalised — or ``SplitValidationError``.

    Normalised: titles and briefs stripped, duplicate dependencies dropped, and
    a step that names no repositories gets every allowed one.
    """
    allowed = [str(repo) for repo in allowed_repositories if str(repo)]
    if not allowed:
        raise SplitValidationError('the plan has no repositories to work in')
    if not drafts:
        raise SplitValidationError('the split has no steps')
    if len(drafts) > MAX_STEPS:
        raise SplitValidationError(
            f'the split has {len(drafts)} steps; at most {MAX_STEPS} are allowed',
        )
    ids = [draft.id for draft in drafts]
    seen: set[str] = set()
    for step_id in ids:
        if not _STEP_ID.match(step_id or ''):
            raise SplitValidationError(
                f'step id {step_id!r} is not usable: use 1-24 lowercase letters, '
                'digits, "-" or "_", starting with a letter',
            )
        if step_id in seen:
            raise SplitValidationError(f'step id {step_id!r} is used twice')
        seen.add(step_id)
    normalised = [_normalised(draft, seen, allowed) for draft in drafts]
    topological_order(normalised)  # raises on a cycle
    return normalised


def topological_order(drafts: Sequence) -> list[str]:
    """Step ids so that every step comes after the steps it depends on.

    Takes drafts or a plan's steps — anything with ``id`` and ``depends_on``.

    Ties keep the order the steps were listed in, so the result is stable.
    Raises ``SplitValidationError`` naming the steps caught in a cycle.
    """
    remaining = {draft.id: set(draft.depends_on) for draft in drafts}
    order: list[str] = []
    while remaining:
        free = [draft.id for draft in drafts if draft.id in remaining and not remaining[draft.id]]
        if not free:
            stuck = ', '.join(draft.id for draft in drafts if draft.id in remaining)
            raise SplitValidationError(f'the steps depend on each other in a circle: {stuck}')
        for step_id in free:
            order.append(step_id)
            del remaining[step_id]
        for deps in remaining.values():
            deps.difference_update(free)
    return order


def dependents_of(step_id: str, drafts: Sequence) -> set[str]:
    """Every step that waits on ``step_id``, directly or through others."""
    waiting: set[str] = set()
    frontier = {step_id}
    while frontier:
        found = {
            draft.id for draft in drafts
            if draft.id not in waiting and frontier & set(draft.depends_on)
        }
        waiting |= found
        frontier = found
    return waiting


def _normalised(draft: StepDraft, ids: set[str], allowed: list[str]) -> StepDraft:
    title = str(draft.title or '').strip()
    brief = str(draft.brief or '').strip()
    if not title:
        raise SplitValidationError(f'step {draft.id!r} has no title')
    if len(title) > MAX_TITLE_CHARS:
        raise SplitValidationError(
            f'step {draft.id!r} has a title over {MAX_TITLE_CHARS} characters',
        )
    if not brief:
        raise SplitValidationError(f'step {draft.id!r} has no brief')
    if len(brief) > MAX_BRIEF_CHARS:
        raise SplitValidationError(
            f'step {draft.id!r} has a brief over {MAX_BRIEF_CHARS} characters',
        )
    depends_on: list[str] = []
    for dep in draft.depends_on:
        if dep == draft.id:
            raise SplitValidationError(f'step {draft.id!r} depends on itself')
        if dep not in ids:
            raise SplitValidationError(f'step {draft.id!r} depends on unknown step {dep!r}')
        if dep not in depends_on:
            depends_on.append(dep)
    repositories: list[str] = []
    for repo in draft.repositories:
        if repo not in allowed:
            raise SplitValidationError(
                f'step {draft.id!r} names repository {repo!r}, which is not one of '
                f'this plan\'s: {", ".join(allowed)}',
            )
        if repo not in repositories:
            repositories.append(repo)
    return StepDraft(
        id=draft.id,
        title=title,
        brief=brief,
        depends_on=tuple(depends_on),
        repositories=tuple(repositories or allowed),
    )
