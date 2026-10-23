"""Every route to done is announced once — and only after the ticket really moved.

``TaskStateService.move_task_to_done`` is the one place: the forget dialog's
"this task is done" and the plan board's "Count as done" both go through it,
for tracker tickets and for kato's own LOCAL tasks. A move that fails announces
nothing (and still raises, so the caller refuses to delete anything); an
observer that fails never turns a done ticket into a failed action.

Real LocalTaskStore + LocalAwareTaskStateService; the tracker is the stand-in.
"""

from __future__ import annotations

import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from kato_core_lib.data_layers.data_access.task_data_access import TaskDataAccess
from kato_core_lib.data_layers.service.local_task_routing import LocalAwareTaskStateService
from kato_core_lib.helpers.late_binding import later
from local_task_core_lib.local_task_core_lib.data.local_task import LocalTaskState
from local_task_core_lib.local_task_core_lib.store import LocalTaskStore
from tests.test_local_task_routing import DONE, PROGRESS, REVIEW, _Tracker


class DoneObserverTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = LocalTaskStore(Path(tmp.name) / 'local_tasks.json')
        self.config = types.SimpleNamespace(
            project='PROJ', assignee='me', issue_states=['Todo', 'Open'],
            progress_state=PROGRESS, review_state=REVIEW, done_state=DONE,
            progress_state_field='State', review_state_field='State',
            done_state_field='State', open_state_field='State',
        )
        self.tracker = _Tracker({'UNA-1': REVIEW})
        self.observer = MagicMock()
        self.states = self._states(self.observer)

    def _states(self, observer):
        return LocalAwareTaskStateService(
            self.config, TaskDataAccess(self.config, self.tracker), self.store, done_observer=observer,
        )

    def test_a_tracker_ticket_moved_to_done_is_announced_once(self) -> None:
        self.states.move_task_to_done('UNA-1')
        self.observer.task_done.assert_called_once_with('UNA-1')

    def test_a_local_task_done_is_announced_too(self) -> None:
        task = self.store.create('local work', 'details', state=LocalTaskState.REVIEW).id
        self.states.move_task_to_done(task)
        self.assertEqual(self.store.get(task).state, LocalTaskState.DONE)
        self.observer.task_done.assert_called_once_with(task)

    def test_a_move_that_fails_raises_and_announces_nothing(self) -> None:
        def refuse(*args, **kwargs):
            raise RuntimeError('workflow forbids it')

        self.tracker.move_issue_to_state = refuse
        with self.assertRaises(RuntimeError):
            self.states.move_task_to_done('UNA-1')
        self.observer.task_done.assert_not_called()

    def test_an_observer_that_fails_never_fails_the_done(self) -> None:
        self.observer.task_done.side_effect = RuntimeError('notes disk full')
        self.states.move_task_to_done('UNA-1')  # no exception

    def test_no_observer_is_fine(self) -> None:
        self._states(None).move_task_to_done('UNA-1')

    def test_the_observer_is_looked_up_when_needed_not_when_built(self) -> None:
        host = types.SimpleNamespace(notes=None)
        states = self._states(later(host, 'notes'))
        host.notes = self.observer  # wired after the state service was built
        states.move_task_to_done('UNA-1')
        self.observer.task_done.assert_called_once_with('UNA-1')

    def test_other_moves_are_not_announced(self) -> None:
        self.states.move_task_to_review('UNA-1')
        self.states.move_task_to_in_progress('UNA-1')
        self.observer.task_done.assert_not_called()


if __name__ == '__main__':
    unittest.main()
