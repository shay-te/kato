"""Was a step's work merged without a pull request? Its destination's history says.

For a sub-task someone finished by pushing straight to the destination (or
merging some other way): a commit naming the task id, made after the task was
created, in the repository's destination branch. Read from the operator's own
clone of the repository through ``GitClientMixin`` (fetch, then log) — never a
bare git call. The id is matched whole: ``PROJ-1`` never matches ``PROJ-12``.
"""

from __future__ import annotations

import re

from plan_queue_core_lib.plan_queue_core_lib.ports import ProbeError

from kato_core_lib.helpers.late_binding import provider_for

_TASK_ID = re.compile(r'^[A-Za-z0-9_]+-[0-9]+$')


class GitHistoryProbe(object):
    def __init__(self, *, repository_service) -> None:
        self._get_repository_service = provider_for(repository_service)

    def merged_in_history(self, task_id: str, repository_id: str, *, since: float) -> bool:
        if not _TASK_ID.match(str(task_id or '')):
            return False  # not a tracker id a commit would name
        service = self._get_repository_service()
        try:
            repository = service.get_repository(repository_id)
            return service.history_mentions(
                repository.local_path, service.destination_branch(repository),
                f'(^|[^A-Za-z0-9_]){task_id}([^0-9]|$)', since=since, repository=repository,
            )
        except Exception as exc:
            raise ProbeError(f'could not read the history of {repository_id}: {exc}') from exc
