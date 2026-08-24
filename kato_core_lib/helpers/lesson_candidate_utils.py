"""Identifiers for per-task lesson candidates.

A candidate is staged under an id that carries its task, so promotion (and the
cleanup that follows a finished task) can find every candidate a task produced
with a prefix match. The prefix and the id are built in two different
subsystems — capture lives on the agent service, promotion lives on the
publish service — so the format lives here, once.
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
