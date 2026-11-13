"""A task kato works on says so on its ticket: In Progress.

THE one place a ticket is moved to In Progress. Each flow used to carry its own
copy (with its own logging and its own idea of when), and the gaps between them
left tickets in Open while kato worked on them:

* kato starts a task by itself (``AgentService``): ``move`` — a ticket that
  cannot move stops the run, because a ticket left in the queue would be
  started a second time by the next scan;
* kato opens a ``kato:wait-planning`` / ``kato:wait-editing`` chat
  (``WaitPlanningService``): ``try_move``, FIRST — before the clone and the
  chat's start, which take a minute or more — and also when its chat already
  runs; a tracker that refuses never blocks the chat;
* a ticket still in the queue whose agent is working right now
  (``move_if_agent_working``, the queue scan): a run that failed moved its
  ticket back to Open (``TaskFailureHandler``), the operator typed "continue",
  and the agent worked on with the ticket still saying Open.

A local task (``LOCAL-<n>``) moves in kato's own store through the same call
(``LocalAwareTaskStateService``).
"""

from __future__ import annotations

from kato_core_lib.data_layers.service.restart_resume.running_agents import is_mid_turn
from kato_core_lib.helpers.logging_utils import configure_logger

#: Why a ticket is moved — the words in the log line.
KATO_STARTED_IT = 'kato started working on it'
CHAT_OPENED_FOR_IT = 'kato opened its chat'
AGENT_IS_WORKING = 'its agent is working on it'


class TaskInProgress(object):
    def __init__(self, task_state_service, *, logger=None) -> None:
        self._states = task_state_service
        self.logger = logger or configure_logger(self.__class__.__name__)

    def move(self, task_id: object, why: str) -> None:
        """Move the task's ticket to In Progress; raises what the tracker raised."""
        task = str(task_id)
        self._states.move_task_to_in_progress(task)
        self.logger.info('task %s moved to in progress: %s', task, why)

    def try_move(self, task_id: object, why: str) -> bool:
        """``move`` that never raises: False — logged — when it did not move."""
        try:
            self.move(task_id, why)
        except Exception:
            self.logger.exception('could not move task %s to in progress (%s)', task_id, why)
            return False
        return True

    def move_if_agent_working(self, task_id: object, session_manager) -> bool:
        """For a ticket in the queue: is its agent working right now? Then the
        ticket moves (``try_move``) and the answer is True — nothing else may
        start on it, a run is already going. False: no agent of it is working.
        """
        if not is_mid_turn(session_manager, str(task_id)):
            return False
        self.try_move(task_id, AGENT_IS_WORKING)
        return True
