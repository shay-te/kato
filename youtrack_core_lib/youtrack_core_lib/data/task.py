"""Task data model."""
from __future__ import annotations


class _RecordField:
    def __init__(self, key: str) -> None:
        self.key = key
        self._storage_name = f'_{key}'

    def __set_name__(self, owner, name) -> None:
        self._storage_name = f'_{name}'

    def __get__(self, instance, owner=None):
        if instance is None:
            return self
        return getattr(instance, self._storage_name, '')

    def __set__(self, instance, value) -> None:
        setattr(instance, self._storage_name, value)


class Task:
    """A single issue / ticket fetched from a task platform."""

    id = _RecordField('id')
    summary = _RecordField('summary')
    description = _RecordField('description')
    branch_name = _RecordField('branch_name')
    tags = _RecordField('tags')

    def __init__(
        self,
        id: str = '',
        summary: str = '',
        description: str = '',
        branch_name: str = '',
        tags: list[str] | None = None,
    ) -> None:
        self.id = id
        self.summary = summary
        self.description = description
        self.branch_name = branch_name
        self.tags = list(tags or [])

    def __repr__(self) -> str:
        return (
            f'Task(id={self.id!r}, summary={self.summary!r}, '
            f'description={self.description!r}, branch_name={self.branch_name!r}, '
            f'tags={self.tags!r})'
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Task):
            return False
        return (
            self.id == other.id
            and self.summary == other.summary
            and self.description == other.description
            and self.branch_name == other.branch_name
            and self.tags == other.tags
        )
