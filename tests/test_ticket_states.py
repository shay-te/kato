"""Each task's ticket state for the tasks menu's status filter (``TicketStates``).

A real ``WorkspaceService`` lists the tasks; the tracker is a recorder that
answers ``get_issues_status`` like YouTrack / Jira (and counts the calls: the
tab list is polled every five seconds, and must never reach a tracker itself).
"""

from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from provider_client_base.provider_client_base.data.subtask import IssueStatus

from kato_core_lib.data_layers.service.ticket_states import BATCH_SIZE, REFRESH_SECONDS, TicketStates
from tests.chaos_lib import build_real_workspace_service


class _Tracker(object):
    def __init__(self, states: dict[str, str]) -> None:
        self.states = states
        self.calls: list[tuple[list[str], str]] = []
        self.fail = None

    def get_issues_status(self, issue_ids, state_field):
        self.calls.append((list(issue_ids), state_field))
        if self.fail:
            raise self.fail
        return {
            issue_id: IssueStatus(issue_id=issue_id, state=self.states.get(issue_id, ''),
                                  exists=issue_id in self.states)
            for issue_id in issue_ids
        }


class _Tasks(object):
    """The task service's three questions: is it local, a local task's state, the tracker."""

    def __init__(self, tracker, local_states=None) -> None:
        self.tracker = tracker
        self.local_states = local_states or {}

    def is_local(self, task_id):
        return str(task_id).startswith('LOCAL-')

    def get_task(self, task_id):
        state = self.local_states.get(task_id)
        return SimpleNamespace(id=task_id, state=state) if state is not None else None

    def subtask_tracker(self):
        return self.tracker


class _States(object):
    @staticmethod
    def state_names():
        return {'field': 'State'}


class TicketStatesTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.workspaces = build_real_workspace_service(Path(tmp.name) / 'workspaces')
        for task_id in ('UNA-1', 'UNA-2', 'LOCAL-1'):
            self.workspaces.create(task_id=task_id, task_summary=task_id, repository_ids=['api'])
        self.tracker = _Tracker({'UNA-1': 'In Progress', 'UNA-2': 'Waiting Deploy'})
        self.tasks = _Tasks(self.tracker, {'LOCAL-1': 'In Review'})
        self.now = 1000.0
        self.background = []
        self.logger = logging.getLogger(f'test.ticket_states.{self.id()}')
        self.states = TicketStates(
            task_service=self.tasks, task_state_service=_States(), workspace_manager=self.workspaces,
            clock=lambda: self.now, run_in_background=self.background.append, logger=self.logger,
        )

    def _run_background(self) -> None:
        while self.background:
            self.background.pop(0)()

    def test_every_task_s_state_from_its_own_ticket_system(self) -> None:
        self.states.refresh()
        self.assertEqual(self.states.states(), {
            'una-1': 'In Progress', 'una-2': 'Waiting Deploy', 'local-1': 'In Review',
        })
        self.assertEqual(self.tracker.calls, [(['UNA-1', 'UNA-2'], 'State')])  # local tasks never reach it

    def test_asking_never_waits_for_the_tracker(self) -> None:
        self.assertEqual(self.states.states(), {})          # nothing read yet: answered at once
        self.assertEqual(self.tracker.calls, [])            # …the read is in the background
        self._run_background()
        self.assertEqual(self.states.states()['una-1'], 'In Progress')

    def test_the_tracker_is_read_at_most_once_a_minute_however_often_asked(self) -> None:
        self.states.states()
        self._run_background()
        for _ in range(20):                                  # the tab list's five-second poll
            self.now += 2.5
            self.states.states()
            self._run_background()
        self.assertEqual(len(self.tracker.calls), 1)
        self.now += REFRESH_SECONDS
        self.states.states()
        self._run_background()
        self.assertEqual(len(self.tracker.calls), 2)

    def test_one_read_at_a_time(self) -> None:
        self.states.states()
        self.now += REFRESH_SECONDS * 5
        self.states.states()                                 # the first read has not run yet
        self.assertEqual(len(self.background), 1)

    def test_a_moved_ticket_shows_its_new_state_on_the_next_read(self) -> None:
        self.states.refresh()
        self.tracker.states['UNA-1'] = 'In Review'
        self.now += REFRESH_SECONDS
        self.states.states()
        self._run_background()
        self.assertEqual(self.states.states()['una-1'], 'In Review')

    def test_a_ticket_the_tracker_does_not_have_and_a_tracker_that_cannot_say_have_no_state(self) -> None:
        del self.tracker.states['UNA-2']
        self.states.refresh()
        self.assertNotIn('una-2', self.states.states())
        self.tasks.tracker = None                            # GitHub / GitLab / Bitbucket issues, no tracker
        self.states.refresh()
        self.assertEqual(self.states.states(), {'local-1': 'In Review'})

    def test_many_tasks_are_read_in_batches(self) -> None:
        for number in range(3, BATCH_SIZE + 10):
            self.workspaces.create(task_id=f'UNA-{number}', task_summary='x', repository_ids=['api'])
        self.states.refresh()
        self.assertEqual([len(ids) for ids, _ in self.tracker.calls], [BATCH_SIZE, 9])

    def test_a_failed_read_keeps_the_last_states_and_is_said_once(self) -> None:
        self.states.refresh()
        self.tracker.fail = RuntimeError('YouTrack answered 503')
        with self.assertLogs(self.logger, level='INFO') as logs:
            self.states.refresh()
            self.states.refresh()
            self.tracker.fail = None
            self.states.refresh()
        self.assertEqual(self.states.states()['una-1'], 'In Progress')
        said = [line for line in logs.output if 'states' in line]
        self.assertEqual(len([line for line in said if '503' in line]), 1)
        self.assertTrue(any('can be read again' in line for line in said))

    def test_a_read_that_breaks_waits_the_minute_before_trying_again(self) -> None:
        self.tasks.get_task = lambda task_id: (_ for _ in ()).throw(RuntimeError('store unreadable'))
        with self.assertLogs(self.logger, level='ERROR'):
            self.states.states()
            self._run_background()
        self.states.states()
        self.assertEqual(self.background, [])                # not again within the minute

    def test_kato_s_own_folders_are_not_tasks(self) -> None:
        records = [SimpleNamespace(task_id='UNA-1'), SimpleNamespace(task_id='lessons'),
                   SimpleNamespace(task_id='lesson-candidates'), SimpleNamespace(task_id='')]
        states = TicketStates(
            task_service=self.tasks, task_state_service=_States(),
            workspace_manager=SimpleNamespace(list_workspaces=lambda: records), logger=self.logger,
        )
        states.refresh()
        self.assertEqual(self.tracker.calls, [(['UNA-1'], 'State')])

    def test_no_workspaces_yet_is_no_states(self) -> None:
        states = TicketStates(task_service=self.tasks, task_state_service=_States(), workspace_manager=None,
                              run_in_background=lambda work: None, logger=self.logger)
        states.refresh()
        self.assertEqual(states.states(), {})
        self.assertEqual(self.tracker.calls, [])


class TabListCarriesTheStateTests(unittest.TestCase):
    def test_each_task_row_carries_its_ticket_state(self) -> None:
        from kato_webserver import app as webapp
        record = SimpleNamespace(task_id='UNA-1', to_dict=lambda: {})
        payload = webapp._workspace_record_to_dict(record, set(), ticket_state_by_task={'una-1': 'In Review'})
        self.assertEqual(payload['ticket_state'], 'In Review')
        self.assertEqual(webapp._workspace_record_to_dict(record, set())['ticket_state'], '')

    def test_no_service_or_a_broken_one_is_no_states(self) -> None:
        from kato_webserver import app as webapp

        def broken():
            raise RuntimeError('boom')

        self.assertEqual(webapp._ticket_states(None), {})
        self.assertEqual(webapp._ticket_states(SimpleNamespace(ticket_states=SimpleNamespace(states=broken))), {})
        self.assertEqual(
            webapp._ticket_states(SimpleNamespace(ticket_states=SimpleNamespace(states=lambda: {'una-1': 'Open'}))),
            {'una-1': 'Open'},
        )
        # Anything but a mapping is no states — the tab list must still be JSON.
        self.assertEqual(webapp._ticket_states(SimpleNamespace(ticket_states=SimpleNamespace(states=object))), {})


if __name__ == '__main__':
    unittest.main()
