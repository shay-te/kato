"""Every ticket move kato makes goes through here: THE one service for it.

A feature that moves a ticket — to In Progress, In Review, Open or Done — calls
``TaskMoves``, never the state service directly. ``tests/test_task_moves.py``
scans the source and fails the build when anything else does, so a new feature
cannot grow its own copy. Copies drifted before: the move to In Progress had
three, each with its own logging and its own idea of when, and the gaps between
them left tickets in Open while kato worked on them.

Works the same for every tracker: the state service turns the move into the
configured field and state name, a local task (``LOCAL-<n>``) moves in kato's
own store (``LocalAwareTaskStateService``), and only the tracker's client knows
how the call is made. Done is also where the "on done" observers hear of it
(``TaskStateService.move_task_to_done``: release notes, Slack, containers).

Two ways to move, the caller's choice:

* ``move`` raises when the tracker refuses — kato's own start (a ticket left in
  the queue would be started a second time), a publish, an operator's Done;
* ``try_move`` never raises, it logs and answers False — a planning chat, a
  failed run's way back to Open: the move must never block what comes next.

And one question only the queue scan asks: ``move_if_agent_working`` — a ticket
still in the queue whose agent is working right now (a run that failed moved it
back to Open, then the operator typed "continue") moves to In Progress.
"""

from __future__ import annotations

from kato_core_lib.data_layers.service.restart_resume.running_agents import is_mid_turn
from kato_core_lib.helpers.logging_utils import configure_logger

#: Where a ticket can be moved — the words in the log line.
IN_PROGRESS = 'in progress'
IN_REVIEW = 'in review'
OPEN = 'open'
DONE = 'done'

_STATE_SERVICE_MOVE = {
    IN_PROGRESS: 'move_task_to_in_progress',
    IN_REVIEW: 'move_task_to_review',
    OPEN: 'move_task_to_open',
    DONE: 'move_task_to_done',
}

#: Why a ticket is moved — also the words in the log line.
KATO_STARTED_IT = 'kato started working on it'
CHAT_OPENED_FOR_IT = 'kato opened its chat'
AGENT_IS_WORKING = 'its agent is working on it'
ITS_RUN_FAILED = 'its run failed'
ITS_PULL_REQUESTS_ARE_OPEN = 'its pull requests are open'
OPERATOR_PRESSED_DONE = 'the operator pressed Done'
OPERATOR_MARKED_IT_DONE = 'the operator marked it done'
COUNTED_DONE_ON_ITS_PLAN = 'it was counted as done on its plan'
HANDED_OVER = 'the operator handed it over to someone else'


class TaskMoves(object):
    def __init__(self, task_state_service, *, logger=None) -> None:
        self._states = task_state_service
        self.logger = logger or configure_logger(self.__class__.__name__)

    def move(self, task_id: object, state: str, why: str) -> None:
        """Move the task's ticket to ``state``; raises what the tracker raised."""
        if state not in _STATE_SERVICE_MOVE:
            raise ValueError(f'a ticket cannot be moved to {state!r}')
        task = str(task_id)
        getattr(self._states, _STATE_SERVICE_MOVE[state])(task)
        self.logger.info('task %s moved to %s: %s', task, state, why)

    def try_move(self, task_id: object, state: str, why: str) -> bool:
        """``move`` that never raises: False — logged — when it did not move."""
        try:
            self.move(task_id, state, why)
        except Exception:
            self.logger.exception('could not move task %s to %s (%s)', task_id, state, why)
            return False
        return True

    def move_if_agent_working(self, task_id: object, session_manager) -> bool:
        """For a ticket in the queue: is its agent working right now? Then the
        ticket moves to In Progress (``try_move``) and the answer is True —
        nothing else may start on it, a run is already going. False: no agent
        of it is working.
        """
        if not is_mid_turn(session_manager, str(task_id)):
            return False
        self.try_move(task_id, IN_PROGRESS, AGENT_IS_WORKING)
        return True
