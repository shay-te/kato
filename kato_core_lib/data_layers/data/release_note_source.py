"""What is known about a finished task when its release note is written."""

from __future__ import annotations

from dataclasses import dataclass, field

from kato_core_lib.helpers.release_notes_document_utils import clean_line


@dataclass(frozen=True)
class ReleaseNoteSource(object):
    task_id: str
    title: str = ''
    description: str = ''
    pr_description: str = ''
    repositories: tuple[str, ...] = field(default_factory=tuple)
    # When kato started the task (epoch): an older pull request on the same
    # branch name belongs to an earlier task that reused the id.
    started_at: float = 0.0
    # The branch its pull requests are on ('' = the task id). Read now with the
    # rest: a branch the operator named is kept in the workspace, and the forget
    # flow deletes that before the links are looked up.
    branch: str = ''

    @property
    def fallback(self) -> str:
        """The line when nothing better is known: the ticket title, else its id."""
        return clean_line(self.title) or self.task_id
