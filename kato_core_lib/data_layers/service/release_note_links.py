"""The pull requests a finished task shipped through, as release-note links.

One job: per repository, the task's pull request from the git host (its
branch is the task id) — merged first, else open, never declined — skipping
any older than the task itself (a task id can be reused, and an earlier task's
merged pull request must not be linked). A host that cannot be asked just
means no link; it never stops the note.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from kato_core_lib.data_layers.data.release_note_source import ReleaseNoteSource
from kato_core_lib.helpers.logging_utils import configure_logger

_PREFERENCE = ('merged', 'open')


class ReleaseNoteLinks(object):
    def __init__(self, *, pull_requests: Callable[[str, str], list] | None, logger=None) -> None:
        """``pull_requests(task_id, repository_id)`` → facts with state, url, created_at."""
        self._pull_requests = pull_requests
        self.logger = logger or configure_logger('ReleaseNoteLinks')

    def links(self, source: ReleaseNoteSource) -> list[tuple[str, str]]:
        """``[(label, url)]`` — labelled ``PR`` for one repository, else by repository."""
        if self._pull_requests is None:
            return []
        found: list[tuple[str, str]] = []
        for repository in source.repositories:
            url = self._best_url(source, repository)
            if url:
                found.append((repository, url))
        if len(found) == 1:
            return [('PR', found[0][1])]
        return found

    def _best_url(self, source: ReleaseNoteSource, repository: str) -> str:
        try:
            facts = list(self._pull_requests(source.task_id, repository) or [])
        except Exception as exc:  # noqa: BLE001 - "could not ask" is just "no link"
            self.logger.info('no pull request link for %s in %s: %s', source.task_id, repository, exc)
            return ''
        current = [fact for fact in facts if not _older_than(fact, source.started_at)]
        for state in _PREFERENCE:
            for fact in current:
                if getattr(fact, 'state', '') == state and getattr(fact, 'url', ''):
                    return str(fact.url)
        return ''


def _older_than(fact, started_at: float) -> bool:
    created = str(getattr(fact, 'created_at', '') or '')
    if not created or not started_at:
        return False
    try:
        return datetime.fromisoformat(created.replace('Z', '+00:00')).timestamp() < started_at
    except ValueError:
        return False
