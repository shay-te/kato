"""A step's pull requests, in any state, straight from the git host.

The step's branch is its task id (kato names every task branch after the task),
so its pull requests are found by branch — nothing has to be recorded when it
is published, and the lookup still works after the task's workspace is gone.
Only pull requests into the repository's destination branch count.
"""

from __future__ import annotations

from plan_queue_core_lib.plan_queue_core_lib.ports import ProbeError, PullRequestFact

from kato_core_lib.helpers.late_binding import provider_for

_STATES = {'open', 'merged', 'declined'}


class KatoPullRequestProbe(object):
    def __init__(self, *, repository_service) -> None:
        self._get_repository_service = provider_for(repository_service)

    def pull_requests(self, task_id: str, repository_id: str) -> list[PullRequestFact]:
        service = self._get_repository_service()
        try:
            repository = service.get_repository(repository_id)
            destination = str(service.destination_branch(repository) or '')
            found = service.pull_request_states(repository, source_branch=task_id)
        except Exception as exc:
            # "Could not ask" must never read as "no pull request".
            raise ProbeError(f'could not read the pull requests of {task_id} in {repository_id}: {exc}') from exc
        facts = []
        for pull_request in found or []:
            state = str(pull_request.get('state', '') or '')
            target = str(pull_request.get('destination_branch', '') or '')
            if state not in _STATES or (destination and target and target != destination):
                continue
            facts.append(PullRequestFact(
                state=state,
                url=str(pull_request.get('url', '') or ''),
                created_at=str(pull_request.get('created_at', '') or ''),
            ))
        return facts
