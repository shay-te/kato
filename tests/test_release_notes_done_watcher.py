"""Tickets closed in the tracker reach the release notes — and nothing else does.

A ticket leaving kato's lists is not proof of done (it may be reassigned or
cancelled), so the tracker is asked. The first sweep only records; a sweep asks
about a few tickets at most; "could not ask" is retried, then given up.
Real state file; the tracker is the only stand-in.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from kato_core_lib.data_layers.service.release_notes_done_watcher import ReleaseNotesDoneWatcher
from provider_client_base.provider_client_base.data.subtask import IssueStatus


class _Tracker(object):
    def __init__(self, states: dict[str, str]) -> None:
        self.states = states
        self.asked: list[list[str]] = []
        self.fail = False

    def get_issues_status(self, issue_ids, state_field):
        self.asked.append(list(issue_ids))
        if self.fail:
            raise RuntimeError('tracker down')
        return {
            issue_id: IssueStatus(issue_id=issue_id, state=self.states[issue_id])
            for issue_id in issue_ids if issue_id in self.states
        }


class ReleaseNotesDoneWatcherTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_path = Path(self._tmp.name) / 'watch.json'
        self.tracker = _Tracker({})
        self.task_service = SimpleNamespace(subtask_tracker=lambda: self.tracker)
        self.states = SimpleNamespace(state_names=lambda: {'field': 'State', 'done': 'Done'})
        self.done: list[str] = []
        self.forgotten: set[str] = set()
        self.enabled = True
        self.watcher = self._watcher()

    def _watcher(self):
        return ReleaseNotesDoneWatcher(
            task_service=self.task_service, task_state_service=self.states,
            on_done=self.done.append, state_path=self.state_path,
            is_local=lambda task_id: task_id.startswith('LOCAL-'),
            is_forgotten=lambda task_id: task_id in self.forgotten,
            enabled=lambda: self.enabled, logger=MagicMock(),
        )

    @staticmethod
    def _workspaces(*ids):
        return {task_id.lower(): task_id for task_id in ids}

    def _sweep(self, live, *workspace_ids):
        self.watcher.observe({task_id.lower() for task_id in live}, self._workspaces(*workspace_ids))

    def test_the_first_sweep_only_records(self) -> None:
        self._sweep({'UNA-1'}, 'UNA-1', 'UNA-2')
        self.assertEqual(self.done, [])
        self.assertEqual(json.loads(self.state_path.read_text())['live'], ['una-1'])
        self.assertEqual(self.tracker.asked, [])

    def test_a_ticket_that_left_the_list_and_is_done_is_handed_on(self) -> None:
        self._sweep({'UNA-1', 'UNA-2'}, 'UNA-1', 'UNA-2')
        self.tracker.states = {'UNA-1': 'Done'}
        self._sweep({'UNA-2'}, 'UNA-1', 'UNA-2')
        self.assertEqual(self.done, ['UNA-1'])
        self.assertEqual(self.tracker.asked, [['UNA-1']])

    def test_a_ticket_that_left_for_another_reason_is_dropped(self) -> None:
        self._sweep({'UNA-1'}, 'UNA-1')
        self.tracker.states = {'UNA-1': 'Open'}  # reassigned, not done
        self._sweep(set(), 'UNA-1')
        self._sweep(set(), 'UNA-1')
        self.assertEqual(self.done, [])
        self.assertEqual(self.tracker.asked, [['UNA-1']])  # asked once, then forgotten

    def test_could_not_ask_is_retried_then_given_up(self) -> None:
        self._sweep({'UNA-1'}, 'UNA-1')
        self.tracker.fail = True
        for _ in range(4):
            self._sweep(set(), 'UNA-1')
        self.assertEqual(len(self.tracker.asked), ReleaseNotesDoneWatcher.MAX_ATTEMPTS)
        self.assertEqual(self.done, [])

    def test_a_retry_that_succeeds_hands_it_on(self) -> None:
        self._sweep({'UNA-1'}, 'UNA-1')
        self.tracker.fail = True
        self._sweep(set(), 'UNA-1')
        self.tracker.fail = False
        self.tracker.states = {'UNA-1': 'done'}  # state names compared without case
        self._sweep(set(), 'UNA-1')
        self.assertEqual(self.done, ['UNA-1'])

    def test_a_ticket_that_comes_back_before_it_is_confirmed_is_not_asked(self) -> None:
        self._sweep({'UNA-1'}, 'UNA-1')
        self.tracker.fail = True
        self._sweep(set(), 'UNA-1')
        self.tracker.fail = False
        self._sweep({'UNA-1'}, 'UNA-1')  # back on the list
        self.assertEqual(len(self.tracker.asked), 1)
        self.assertEqual(self.done, [])

    def test_local_forgotten_and_workspace_less_tasks_are_not_asked_about(self) -> None:
        self.forgotten = {'UNA-3'}
        self._sweep({'LOCAL-1', 'UNA-3', 'UNA-4'}, 'LOCAL-1', 'UNA-3')
        self._sweep(set(), 'LOCAL-1', 'UNA-3')
        self.assertEqual(self.tracker.asked, [])

    def test_at_most_a_few_tickets_are_asked_about_per_sweep(self) -> None:
        ids = [f'UNA-{n}' for n in range(1, 9)]
        self._sweep(set(ids), *ids)
        self.tracker.states = {task_id: 'Done' for task_id in ids}
        self._sweep(set(), *ids)
        self.assertEqual(len(self.tracker.asked[0]), ReleaseNotesDoneWatcher.MAX_PER_SWEEP)
        self._sweep(set(), *ids)
        self.assertEqual(sorted(self.done), sorted(ids))

    def test_a_tracker_that_cannot_report_states_is_skipped_and_said_once(self) -> None:
        self.task_service.subtask_tracker = lambda: None
        self._sweep({'UNA-1', 'UNA-2'}, 'UNA-1', 'UNA-2')
        self._sweep({'UNA-2'}, 'UNA-1', 'UNA-2')
        self._sweep(set(), 'UNA-1', 'UNA-2')
        self.assertEqual(self.done, [])
        self.assertEqual(self.watcher.logger.info.call_count, 1)

    def test_switched_off_only_records_so_switching_on_does_not_flood(self) -> None:
        self.enabled = False
        self._sweep({'UNA-1'}, 'UNA-1')
        self._sweep(set(), 'UNA-1')
        self.enabled = True
        self.tracker.states = {'UNA-1': 'Done'}
        self._sweep(set(), 'UNA-1')
        self.assertEqual(self.done, [])

    def test_what_it_remembers_survives_a_restart(self) -> None:
        self._sweep({'UNA-1'}, 'UNA-1')
        self.tracker.states = {'UNA-1': 'Done'}
        self.watcher = self._watcher()  # kato restarted
        self._sweep(set(), 'UNA-1')
        self.assertEqual(self.done, ['UNA-1'])

    def test_a_damaged_state_file_starts_over_quietly(self) -> None:
        self.state_path.write_text('{nope', encoding='utf-8')
        self._sweep({'UNA-1'}, 'UNA-1')
        self.assertEqual(json.loads(self.state_path.read_text())['live'], ['una-1'])

    def test_nothing_ever_breaks_the_sweep(self) -> None:
        self.states.state_names = MagicMock(side_effect=RuntimeError('config'))
        self._sweep({'UNA-1'}, 'UNA-1')
        self._sweep(set(), 'UNA-1')  # no exception
        self.watcher.logger.exception.assert_called_once()


if __name__ == '__main__':
    unittest.main()
