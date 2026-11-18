"""The pull requests a finished task shipped through, as release-note links.

One job: per repository, the task's pull request from the git host (found by
the task's branch) — the NEWEST one, merged or open, never declined — skipping
any older than the task itself (a task id can be reused, and an earlier task's
merged pull request must not be linked). A host that cannot be asked just
means no link; it never stops the note.

The newest, not "merged first": a task continued after its first pull request
merged gets a new one on the same branch, and the Slack post kept linking the
old merged one (2026-11-18). With no creation times to compare, an open pull
request is the newer: a branch's merged one and an open one mean it was opened
after. ``since`` (a repeat Slack post: when it was last posted) leaves out a
merged pull request opened before then — the earlier round's, already posted.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from kato_core_lib.data_layers.data.release_note_source import ReleaseNoteSource
from kato_core_lib.helpers.logging_utils import configure_logger

_LINKED_STATES = ('merged', 'open')


class ReleaseNoteLinks(object):
    def __init__(self, *, pull_requests: Callable[..., list] | None, logger=None) -> None:
        """``pull_requests(branch, repository_id, task_id=...)`` → facts with
        state, url, created_at. The task id lets a plan sub-task's pull request
        into its plan's branch count."""
        self._pull_requests = pull_requests
        self.logger = logger or configure_logger('ReleaseNoteLinks')

    def links(self, source: ReleaseNoteSource, *, since: float = 0.0) -> list[tuple[str, str]]:
        """``[(label, url)]`` — labelled ``PR`` for one repository, else by repository."""
        if self._pull_requests is None:
            return []
        found: list[tuple[str, str]] = []
        for repository in source.repositories:
            url = self._best_url(source, repository, since)
            if url:
                found.append((repository, url))
        if len(found) == 1:
            return [('PR', found[0][1])]
        return found

    def _best_url(self, source: ReleaseNoteSource, repository: str, since: float) -> str:
        try:
            facts = list(self._pull_requests(
                source.branch or source.task_id, repository, task_id=source.task_id,
            ) or [])
        except Exception as exc:  # noqa: BLE001 - "could not ask" is just "no link"
            self.logger.info('no pull request link for %s in %s: %s', source.task_id, repository, exc)
            return ''
        linked = [
            fact for fact in facts
            if getattr(fact, 'state', '') in _LINKED_STATES and getattr(fact, 'url', '')
            and not _older_than(fact, source.started_at)
            and not (since and fact.state == 'merged' and _older_than(fact, since))
        ]
        if not linked:
            return ''
        return str(max(linked, key=_newness).url)


def _created(fact) -> float | None:
    created = str(getattr(fact, 'created_at', '') or '')
    if not created:
        return None
    try:
        return datetime.fromisoformat(created.replace('Z', '+00:00')).timestamp()
    except ValueError:
        return None


def _older_than(fact, moment: float) -> bool:
    created = _created(fact)
    return bool(moment) and created is not None and created < moment


def _newness(fact) -> tuple[float, bool]:
    """Later created first; then open before merged (no times to compare)."""
    created = _created(fact)
    return (created if created is not None else float('-inf'), fact.state == 'open')
