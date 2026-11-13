"""Hand a task over to a teammate, so THEIR kato picks it up.

The operator picks someone the ticket can be assigned to (the header's "Hand
over"). Kato then, in this order:

1. assigns the ticket to them, and reads the assignee back
   (``tracker_assignment.assign_verified``) — nothing after runs if this fails;
2. tags it ``kato:wait-editing``, so their kato opens it as an editing chat and
   waits for them instead of implementing it unasked — nothing after runs if
   this fails either: a ticket released without the tag would be run
   autonomously by a kato its owner is not watching;
3. clears what would stop their kato from picking it up: deletes the blocking
   comments this kato's account wrote (``pickup_block.clear_pickup_block``) —
   never a person's;
4. posts one comment saying who has it now, signed with this kato's name. It is
   also an operator override (``kato: retry approved …``): should a delete in 3
   have failed, or a tracker refused it, every kato version still reads the
   earlier blocks as cleared;
5. moves the ticket to Open (``TaskMoves``) — the queue their kato reads, LAST,
   so their kato sees the ticket only once all of the above is on it;
6. stops this task's agent here (the header's Stop). The workspace is left as
   it is: kato never deletes on its own.

Refused while this task's agent is mid-turn (``TaskBusyError``), and for a task
no one else's kato can see (a local task, a tracker that cannot hand over —
``HandoverUnavailableError``). Every step's outcome is reported.
"""

from __future__ import annotations

from collections.abc import Callable

from kato_core_lib.data_layers.data.fields import TaskTags
from kato_core_lib.data_layers.service.pickup_block import clear_pickup_block
from kato_core_lib.data_layers.service.restart_resume.running_agents import is_mid_turn
from kato_core_lib.data_layers.service.task_moves import HANDED_OVER, OPEN, TaskMoves
from kato_core_lib.data_layers.service.tracker_assignment import assign_verified
from kato_core_lib.helpers.agent_name_utils import agent_name
from kato_core_lib.helpers.logging_utils import configure_logger

HANDED_OVER_COMMENT = (
    'kato: retry approved — handed over to {name} by {by}. Their kato will pick it up.'
)

STEP_ASSIGN = 'assign'
STEP_TAG = 'tag'
STEP_COMMENTS = 'comments'
STEP_NOTE = 'note'
STEP_OPEN = 'open'
STEP_STOP = 'stop'

LOCAL_TASK_REASON = 'this task was created in kato and lives only here — no one else\'s kato can see it'
NO_HANDOVER_REASON = 'the issue tracker ({provider}) cannot hand a ticket over from kato yet'


class HandoverUnavailableError(ValueError):
    pass


class TaskBusyError(RuntimeError):
    pass


class TaskHandover(object):
    def __init__(
        self,
        *,
        task_service,
        task_state_service,
        session_manager,
        signed_by: Callable[[], str] = agent_name,
        logger=None,
    ) -> None:
        self._task_service = task_service
        self._task_state_service = task_state_service
        self._session_manager = session_manager
        self._signed_by = signed_by
        self.logger = logger or configure_logger(self.__class__.__name__)

    def unavailable_reason(self, task_id: str) -> str:
        """Why ``task_id`` cannot be handed over ('' = it can)."""
        if self._tracker(task_id) is not None:
            return ''
        is_local = getattr(self._task_service, 'is_local', None)
        if callable(is_local) and is_local(task_id):
            return LOCAL_TASK_REASON
        return NO_HANDOVER_REASON.format(provider=self._provider())

    def users(self, task_id: str) -> list[dict[str, str]]:
        """Everyone the ticket can be handed to: ``[{handle, name}]``."""
        tracker = self._available_tracker(task_id)
        return [{'handle': user.handle, 'name': user.name} for user in tracker.list_assignable_users(task_id)]

    def hand_over(self, task_id: str, handle: str, name: str = '', *, stop_agent: Callable[[str], str]) -> dict:
        task = str(task_id or '').strip()
        handle = str(handle or '').strip()
        if not handle:
            raise ValueError('pick who the task goes to')
        tracker = self._available_tracker(task)
        if is_mid_turn(self._session_manager, task):
            raise TaskBusyError(f'{task}\'s agent is working — wait for it to finish, or stop it first')
        person = str(name or '').strip() or handle
        steps: list[dict] = []
        if not self._run(steps, STEP_ASSIGN, lambda: assign_verified(tracker, task, handle)):
            return _result(task, handle, person, steps)
        if not self._run(steps, STEP_TAG, lambda: self._task_service.add_tag(task, TaskTags.WAIT_EDITING)):
            return _result(task, handle, person, steps)
        self._run(steps, STEP_COMMENTS, lambda: self._clear_comments(tracker, task))
        self._run(steps, STEP_NOTE, lambda: self._task_service.add_comment(
            task, HANDED_OVER_COMMENT.format(name=person, by=self._signed_by() or 'kato'),
        ))
        self._run(steps, STEP_OPEN, lambda: TaskMoves(self._task_state_service, logger=self.logger).move(
            task, OPEN, HANDED_OVER,
        ))
        self._run(steps, STEP_STOP, lambda: stop_agent(task))
        return _result(task, handle, person, steps)

    def _clear_comments(self, tracker, task: str) -> str:
        cleared = clear_pickup_block(tracker, self._task_service, task, delete_own=True)
        if cleared.problems:
            raise RuntimeError('; '.join(cleared.problems))
        count = len(cleared.deleted)
        if not count:
            return 'nothing of kato\'s blocked it'
        return f'deleted {count} of kato\'s comment{"s" if count != 1 else ""}'

    def _run(self, steps: list[dict], step: str, work: Callable[[], object]) -> bool:
        try:
            detail = work()
        except Exception as exc:
            self.logger.exception('handing over: step %s failed', step)
            steps.append({'step': step, 'ok': False, 'detail': str(exc) or exc.__class__.__name__})
            return False
        steps.append({'step': step, 'ok': True, 'detail': detail if isinstance(detail, str) else ''})
        return True

    def _available_tracker(self, task_id: str):
        reason = self.unavailable_reason(task_id)
        if reason:
            raise HandoverUnavailableError(reason)
        return self._tracker(task_id)

    def _tracker(self, task_id: str):
        lookup = getattr(self._task_service, 'handover_tracker', None)
        return lookup(task_id) if callable(lookup) else None

    def _provider(self) -> str:
        return str(getattr(self._task_service, 'provider_name', '') or 'this one')


def _result(task: str, handle: str, name: str, steps: list[dict]) -> dict:
    assigned = any(step['step'] == STEP_ASSIGN and step['ok'] for step in steps)
    problems = [step for step in steps if not step['ok']]
    if not assigned:
        status = 'failed'
    elif problems:
        status = 'handed_over_with_problems'
    else:
        status = 'handed_over'
    return {'task_id': task, 'status': status, 'assignee': {'handle': handle, 'name': name}, 'steps': steps}
