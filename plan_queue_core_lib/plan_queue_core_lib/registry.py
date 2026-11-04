"""Every plan in memory, each change saved, one lock for all of it.

All changes go through ``mutate``: the change runs under the lock on the live
plan and is saved before the lock is released, so two writers (the operator and
the background tick) can never interleave halfway through a change. Slow work —
an agent run, a call to a tracker — happens OUTSIDE the lock on a copy, and its
result is applied with ``mutate`` only if the plan is still in the state the
work started from.
"""

from __future__ import annotations

import threading
from typing import Callable, TypeVar

from plan_queue_core_lib.plan_queue_core_lib.data.plan import Plan
from plan_queue_core_lib.plan_queue_core_lib.errors import ErrorKind, PlanQueueError
from plan_queue_core_lib.plan_queue_core_lib.store import PlanQueueStore

T = TypeVar('T')


class PlanRegistry(object):
    def __init__(self, store: PlanQueueStore, *, clock: Callable[[], float]) -> None:
        self._store = store
        self._clock = clock
        self._lock = threading.RLock()
        self._plans: dict[str, Plan] = {plan.plan_id: plan for plan in store.load_all()}

    @property
    def store(self) -> PlanQueueStore:
        return self._store

    def add(self, plan: Plan) -> Plan:
        with self._lock:
            self._plans[plan.plan_id] = plan
            self._store.save(plan)
            return _copy(plan)

    def remove(self, plan_id: str) -> None:
        with self._lock:
            self._plans.pop(plan_id, None)
            self._store.delete(plan_id)

    def get(self, plan_id: str) -> Plan:
        """A copy of the plan, or ``PlanQueueError(NOT_FOUND)``."""
        with self._lock:
            return _copy(self._live(plan_id))

    def find(self, plan_id: str) -> Plan | None:
        with self._lock:
            plan = self._plans.get(plan_id)
            return _copy(plan) if plan is not None else None

    def all(self) -> list[Plan]:
        with self._lock:
            plans = sorted(self._plans.values(), key=lambda plan: plan.created_at)
            return [_copy(plan) for plan in plans]

    def source_of_task(self, task_id: str) -> str:
        """The task the newest plan with a step running as ``task_id`` was
        split from ('' when no plan has one). Read in place, copying nothing:
        a host asks this on paths it takes many times a minute."""
        key = str(task_id or '').strip().lower()
        if not key:
            return ''
        with self._lock:
            newest = None
            for plan in self._plans.values():
                if plan.step_for_task(key) is None:
                    continue
                if newest is None or plan.created_at >= newest.created_at:
                    newest = plan
            return newest.source_task_id if newest is not None else ''

    def step_task_sources(self) -> dict[str, str]:
        """``task id (lower-case) -> the task its plan was split from``, for
        every task a plan's step runs or ran as — any plan state; the newest
        plan wins. One pass, for a list a host polls every few seconds."""
        with self._lock:
            found: dict[str, str] = {}
            for plan in sorted(self._plans.values(), key=lambda entry: entry.created_at):
                for step in plan.steps:
                    for task_id in (step.task_id, *step.previous_task_ids):
                        key = str(task_id or '').strip().lower()
                        if key:
                            found[key] = plan.source_task_id
            return found

    def mutate(self, plan_id: str, change: Callable[[Plan], T]) -> T:
        """Apply ``change`` to the live plan and save it if it changed.

        ``change`` may raise ``PlanQueueError`` to refuse; the plan is then
        restored exactly as it was and nothing is saved. A change that leaves
        the plan as it was writes nothing (the tick calls this every few
        seconds for every plan).
        """
        with self._lock:
            plan = self._live(plan_id)
            before = plan.to_dict()
            try:
                result = change(plan)
            except BaseException:
                self._plans[plan_id] = Plan.from_dict(before)
                raise
            if plan.to_dict() != before:
                plan.updated_at = self._clock()
                self._store.save(plan)
            return result

    def _live(self, plan_id: str) -> Plan:
        plan = self._plans.get(plan_id)
        if plan is None:
            raise PlanQueueError(f'there is no plan {plan_id}', ErrorKind.NOT_FOUND)
        return plan


def _copy(plan: Plan) -> Plan:
    return Plan.from_dict(plan.to_dict())
