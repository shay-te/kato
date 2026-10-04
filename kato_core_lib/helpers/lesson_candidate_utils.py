"""Identifiers for per-task lesson candidates.

A candidate is staged under an id that carries its task, so promotion (and the
cleanup that follows a finished task) can find every candidate a task produced
with a prefix match. The prefix and the id are built in several different
subsystems — capture lives on the agent service and the comment service,
promotion lives on the publish service, release lives on the delete endpoint —
so the format lives here, once.

Two families, both keyed on the task id first:

    task__<task-id>__<source>__<hex>            an operator chat prompt
    comment__<task-id>__<comment-id>__<hex>     an operator diff comment
"""

from __future__ import annotations

import uuid


def task_lesson_candidate_prefix(task_id: str) -> str:
    """The id prefix shared by every candidate staged for ``task_id``."""
    return f'task__{str(task_id or "").strip()}__'


def task_lesson_candidate_id(task_id: str, source: str) -> str:
    """A unique candidate id for ``task_id``, tagged with where it came from."""
    return (
        f'{task_lesson_candidate_prefix(task_id)}'
        f'{str(source or "prompt").strip()}__{uuid.uuid4().hex}'
    )


def task_comment_lesson_candidate_prefix(task_id: str) -> str:
    """The id prefix shared by every COMMENT candidate staged for ``task_id``."""
    return f'comment__{str(task_id or "").strip()}__'


def comment_lesson_candidate_prefix(task_id: str, comment_id: str) -> str:
    """The id prefix shared by every candidate staged for one diff comment."""
    return (
        f'{task_comment_lesson_candidate_prefix(task_id)}'
        f'{str(comment_id or "").strip()}__'
    )


def comment_lesson_candidate_id(task_id: str, comment_id: str) -> str:
    """A unique candidate id for one diff comment on ``task_id``."""
    return (
        f'{comment_lesson_candidate_prefix(task_id, comment_id)}'
        f'{uuid.uuid4().hex}'
    )


def all_task_lesson_candidate_prefixes(task_id: str) -> tuple[str, ...]:
    """Every prefix a task's candidates can be staged under.

    What "all of this task's candidates" means, for the caller settling them
    when the task goes away. A new candidate family must be added here, or
    its files outlive the task that produced them — which is how the
    candidates directory came to hold dozens of files for tasks that had been
    deleted months earlier.
    """
    return (
        task_lesson_candidate_prefix(task_id),
        task_comment_lesson_candidate_prefix(task_id),
    )
