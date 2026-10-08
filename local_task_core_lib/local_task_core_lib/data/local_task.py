"""A task kept on this machine: what an issue tracker would hold, minus the tracker.

The shape mirrors what a host reads off a tracker ticket — an id, a summary, a
description, tags, a workflow state and a comment thread — so a host can treat
a local task as one more ticket and run it through the same pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class LocalTaskState(str, Enum):
    """Where the task is in its lifecycle — the columns of a tracker board."""

    OPEN = 'open'
    IN_PROGRESS = 'in_progress'
    REVIEW = 'review'
    DONE = 'done'


@dataclass(frozen=True)
class LocalComment(object):
    """One comment on the task's thread, oldest first."""

    body: str
    author: str = ''
    # A stable handle for "who wrote this", as a tracker's account id is.
    author_id: str = ''
    created_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            'body': self.body,
            'author': self.author,
            'author_id': self.author_id,
            'created_at': self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LocalComment:
        return cls(
            body=str(data.get('body') or ''),
            author=str(data.get('author') or ''),
            author_id=str(data.get('author_id') or ''),
            created_at=float(data.get('created_at') or 0.0),
        )


@dataclass
class LocalTask(object):
    id: str
    summary: str
    description: str = ''
    tags: list[str] = field(default_factory=list)
    state: LocalTaskState = LocalTaskState.OPEN
    comments: list[LocalComment] = field(default_factory=list)
    created_at: float = 0.0
    updated_at: float = 0.0

    def has_tag(self, tag: str) -> bool:
        wanted = tag.lower()
        return any(existing.lower() == wanted for existing in self.tags)

    def to_dict(self) -> dict[str, Any]:
        return {
            'id': self.id,
            'summary': self.summary,
            'description': self.description,
            'tags': list(self.tags),
            'state': self.state.value,
            'comments': [comment.to_dict() for comment in self.comments],
            'created_at': self.created_at,
            'updated_at': self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LocalTask:
        """Read one stored task; a field written by a newer version is ignored
        and a missing one takes its default."""
        try:
            state = LocalTaskState(data.get('state') or LocalTaskState.OPEN.value)
        except ValueError:
            state = LocalTaskState.OPEN
        return cls(
            id=str(data['id']),
            summary=str(data.get('summary') or ''),
            description=str(data.get('description') or ''),
            tags=[str(tag) for tag in data.get('tags') or [] if str(tag).strip()],
            state=state,
            comments=[
                LocalComment.from_dict(item)
                for item in data.get('comments') or []
                if isinstance(item, dict)
            ],
            created_at=float(data.get('created_at') or 0.0),
            updated_at=float(data.get('updated_at') or 0.0),
        )
