"""A tracker outage must not be reported as "you are not the assignee".

``find_assigned_or_review_task`` walks three queues and swallows every
per-queue error, so two very different situations produce the same ``None``:

* the tracker answered, and the task genuinely is not in any queue
* every tracker call FAILED — expired token, rate limit, DNS, proxy

``sync_task_repositories`` turned both into

    could not find UNA-2669 on the ticket platform — check that you are
    still the assignee and that the ticket is reachable from kato's
    configured queues

which, during an outage, sends the operator to audit ticket permissions
while kato is simply unable to talk to the tracker at all. The same
swallowing is why "kato is not pulling new tasks" and "sync repositories
failed" look like separate faults when they are one.

These tests pin that the two cases produce DIFFERENT, accurate messages.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from kato_core_lib.data_layers.service.task_repository_service import (
    TaskRepositoryService,
)


class _Workspace:
    """A workspace exists — the sync gets past its earlier guards."""

    repository_ids = ['form-core-lib']


def _service(*, task_service):
    return TaskRepositoryService(
        repository_service=SimpleNamespace(),
        task_service=task_service,
        workspace_manager=SimpleNamespace(get=lambda task_id: _Workspace()),
        logger=SimpleNamespace(
            info=lambda *a, **k: None, warning=lambda *a, **k: None,
            exception=lambda *a, **k: None, error=lambda *a, **k: None,
            debug=lambda *a, **k: None,
        ),
    )


def _exploding_task_service(exc):
    def boom(*args, **kwargs):  # noqa: ARG001
        raise exc

    return SimpleNamespace(
        list_all_assigned_tasks=boom,
        get_assigned_tasks=boom,
        get_review_tasks=boom,
    )


def _empty_task_service():
    return SimpleNamespace(
        list_all_assigned_tasks=lambda: [],
        get_assigned_tasks=lambda: [],
        get_review_tasks=lambda: [],
    )


class PlatformOutageIsNotAMissingTicketTests(unittest.TestCase):
    def test_an_outage_is_NOT_reported_as_an_assignee_problem(self) -> None:
        # THE REPORT. Every queue raising is a connection/credentials fault;
        # nothing whatsoever is known about the ticket.
        service = _service(
            task_service=_exploding_task_service(RuntimeError('401 Unauthorized')),
        )
        result = service.sync_task_repositories('UNA-2669')
        error = str(result.get('error') or '')
        self.assertNotIn('assignee', error)
        self.assertIn('could not reach the ticket platform', error)

    def test_the_outage_message_carries_the_underlying_reason(self) -> None:
        # "Could not reach the platform" is not actionable on its own —
        # 401, 429 and a DNS failure need different responses.
        service = _service(
            task_service=_exploding_task_service(RuntimeError('429 Too Many Requests')),
        )
        error = str(service.sync_task_repositories('UNA-2669').get('error') or '')
        self.assertIn('429 Too Many Requests', error)

    def test_the_outage_message_says_it_is_not_the_ticket_s_fault(self) -> None:
        # The operator's next action differs entirely between the two cases,
        # so the message has to steer it.
        service = _service(
            task_service=_exploding_task_service(RuntimeError('boom')),
        )
        error = str(service.sync_task_repositories('UNA-2669').get('error') or '')
        self.assertIn('credentials', error)

    def test_a_genuinely_missing_ticket_still_says_so(self) -> None:
        # The other half. This message is CORRECT when the queues answered
        # and simply did not contain the task — the fix must not blur the
        # two into one vague string.
        service = _service(task_service=_empty_task_service())
        error = str(service.sync_task_repositories('UNA-2669').get('error') or '')
        self.assertIn('could not find UNA-2669', error)
        self.assertIn('assignee', error)
        self.assertNotIn('could not reach', error)

    def test_a_partial_outage_still_finds_the_task(self) -> None:
        # One queue down must not fail the sync when another queue can
        # answer — the walk exists precisely to tolerate that.
        task = SimpleNamespace(id='UNA-2669', tags=[], description='')

        def boom():
            raise RuntimeError('502 Bad Gateway')

        service = _service(task_service=SimpleNamespace(
            list_all_assigned_tasks=boom,
            get_assigned_tasks=lambda: [task],
            get_review_tasks=lambda: [],
        ))
        found = service._lookup_task_for_sync('UNA-2669', [])
        self.assertIs(found, task)

    def test_failures_are_only_collected_when_asked_for(self) -> None:
        # ``failures`` is optional: add_task_repository calls the lookup
        # without it and must keep working.
        service = _service(
            task_service=_exploding_task_service(RuntimeError('boom')),
        )
        self.assertIsNone(service._lookup_task_for_sync('UNA-2669'))


class StaysOnMasterIsReportedNotSilentTests(unittest.TestCase):
    """Repos that never reached branch prep must not look like a success.

    ``_put_new_clones_on_the_task_branch`` returned ``[]`` — the "no
    failures" value — whenever provisioning handed back nothing. The sync
    toast then said the repos were added while every one of them sat on the
    remote's default branch, and push/PR silently skipped them because the
    task branch did not exist: "he will clone all the repos but will not
    create the branch... all the repos will sit on master".
    """

    def _svc(self):
        return _service(task_service=_empty_task_service())

    def test_missing_provisioned_clones_are_reported_as_failures(self) -> None:
        service = self._svc()
        failures = service._put_new_clones_on_the_task_branch(
            'UNA-2669',
            SimpleNamespace(id='UNA-2669'),
            [],  # provisioning came back empty
            [SimpleNamespace(id='form-core-lib')],
        )
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]['repository_id'], 'form-core-lib')
        self.assertIn('default branch', failures[0]['error'])

    def test_nothing_to_add_is_still_not_a_failure(self) -> None:
        # The legitimate empty case: no repos were missing, so no branch
        # prep was needed and there is nothing to report.
        service = self._svc()
        self.assertEqual(
            service._put_new_clones_on_the_task_branch(
                'UNA-2669', SimpleNamespace(id='UNA-2669'), [], [],
            ),
            [],
        )


if __name__ == '__main__':
    unittest.main()
