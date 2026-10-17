"""Scripted stand-ins for the host's ports, shared by the plan queue tests."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Sequence

from plan_queue_core_lib.plan_queue_core_lib.plan_record import parse_record, render_record
from plan_queue_core_lib.plan_queue_core_lib.ports import (
    LaunchRequest,
    ProbeError,
    PullRequestFact,
    TaskObservation,
    TaskPhase,
)
from plan_queue_core_lib.plan_queue_core_lib.proposer_prompt import SPLIT_CLOSE, SPLIT_OPEN


def wait_until(predicate, timeout: float = 5.0, interval: float = 0.005) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class ManualClock(object):
    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def split_reply(steps: Sequence[dict], notes: str = '', prefix: str = 'Here is the split.') -> str:
    return f'{prefix}\n{SPLIT_OPEN}\n{json.dumps({"steps": list(steps), "notes": notes})}\n{SPLIT_CLOSE}'


def step_spec(step_id: str, depends_on=(), repositories=(), title: str = '', brief: str = '') -> dict:
    return {
        'id': step_id,
        'title': title or f'Do {step_id}',
        'brief': brief or f'Brief for {step_id}.',
        'depends_on': list(depends_on),
        'repositories': list(repositories),
    }


class FakeProposer(object):
    """Answers with the scripted replies in order; the last one repeats."""

    def __init__(self, *replies, error: Exception | None = None, gate: threading.Event | None = None) -> None:
        self.replies = list(replies)
        self.error = error
        self.gate = gate           # when set, propose() blocks until the gate opens
        self.prompts: list[str] = []
        self.calls: list[dict] = []

    def propose(self, prompt, *, source_task_id, cancel_event, model=''):
        self.prompts.append(prompt)
        self.calls.append({'source_task_id': source_task_id, 'model': model})
        if self.gate is not None:
            while not self.gate.is_set() and not cancel_event.is_set():
                self.gate.wait(0.01)
        if self.error is not None:
            raise self.error
        if not self.replies:
            return ''
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


class FakeWording(object):
    def wrap_untrusted(self, text, *, source):
        return f'<untrusted source="{source}">\n{text}\n</untrusted>'

    def guidance(self):
        return 'Host guidance: keep it small.'


class FakeLauncher(object):
    """A host's launcher. ``creates_at_approval`` True behaves like a tracker
    (tasks created up front, released by assignment); False like a host that
    creates and starts a task in one go at release."""

    def __init__(self, *, creates_at_approval: bool = True, prefix: str = 'T') -> None:
        self.creates_at_approval = creates_at_approval
        self.prefix = prefix
        self.is_available = True
        self.prepared: list[LaunchRequest] = []
        self.released: list[tuple[LaunchRequest, str]] = []
        self.withdrawn: list[tuple[str, str]] = []
        self.prepare_errors: dict[str, Exception] = {}
        self.release_errors: dict[str, Exception] = {}
        self.prepare_returns_empty: set[str] = set()
        self.counted: list[str] = []
        self._counter = 100

    def available(self):
        return self.is_available

    def _new_id(self) -> str:
        self._counter += 1
        return f'{self.prefix}-{self._counter}'

    def prepare(self, request):
        self.prepared.append(request)
        if request.step_id in self.prepare_errors:
            raise self.prepare_errors.pop(request.step_id)
        if request.step_id in self.prepare_returns_empty:
            return ''
        return self._new_id()

    def release(self, request, task_id):
        self.released.append((request, task_id))
        if request.step_id in self.release_errors:
            raise self.release_errors.pop(request.step_id)
        if self.creates_at_approval:
            return task_id
        return self._new_id()

    def withdraw(self, task_id, reason):
        self.withdrawn.append((task_id, reason))

    def counted_done(self, task_id):
        self.counted.append(task_id)


class FakeProgress(object):
    def __init__(self) -> None:
        self.phases: dict[str, TaskPhase] = {}
        self.details: dict[str, str] = {}
        self.error: Exception | None = None
        self.asked: list[list[str]] = []

    def set(self, task_id: str, phase: TaskPhase, detail: str = '') -> None:
        self.phases[task_id] = phase
        if detail:
            self.details[task_id] = detail

    def observe(self, task_ids):
        self.asked.append(list(task_ids))
        if self.error is not None:
            raise self.error
        return {
            task_id: TaskObservation(self.phases[task_id], self.details.get(task_id, ''))
            for task_id in task_ids if task_id in self.phases
        }


class FakeProbe(object):
    def __init__(self) -> None:
        self.facts: dict[tuple[str, str], list[PullRequestFact]] = {}
        self.failing: set[tuple[str, str]] = set()
        self.calls: list[tuple[str, str]] = []

    def set(self, task_id: str, repository_id: str, *facts: PullRequestFact) -> None:
        self.facts[(task_id, repository_id)] = list(facts)

    def pull_requests(self, task_id, repository_id):
        self.calls.append((task_id, repository_id))
        if (task_id, repository_id) in self.failing:
            raise ProbeError('the host is down')
        return list(self.facts.get((task_id, repository_id), []))



class FakeTracker(object):
    """ONE shared tracker that several hosts see: sub-tasks with a state and an
    assignee, and the plan records posted on a task. Each host gets its own
    launcher / publisher (acting as its user) over the same tickets."""

    def __init__(self, clock) -> None:
        self.clock = clock
        self.tasks: dict[str, dict] = {}
        self.records: dict[str, list] = {}
        self.cancelled: list[str] = []
        self.counted_errors: dict[str, Exception] = {}
        self.publish_error: Exception | None = None
        self.read_error: Exception | None = None
        self._counter = 200

    # ----- what people do on the tracker -----

    def take(self, task_id: str, user: str, name: str = '') -> None:
        self.tasks[task_id].update(assignee=user, name=name or user.title())

    def hand_back(self, task_id: str) -> None:
        self.tasks[task_id].update(assignee='', name='', state='todo')

    def move(self, task_id: str, state: str) -> None:
        self.tasks[task_id]['state'] = state

    def delete(self, task_id: str) -> None:
        del self.tasks[task_id]

    def post(self, source: str, record) -> None:
        self.records.setdefault(source, []).append(record)

    # ----- a host's view -----

    def launcher(self, user: str) -> 'TrackerLauncher':
        return TrackerLauncher(self, user)

    def publisher(self, user: str) -> 'TrackerPublisher':
        return TrackerPublisher(self, user)

    def observe(self, task_ids):
        found = {}
        for task_id in task_ids:
            task = self.tasks.get(task_id)
            if task is None:
                found[task_id] = TaskObservation(TaskPhase.MISSING)
                continue
            found[task_id] = TaskObservation(
                _tracker_phase(task), assignee=task['assignee'], assignee_name=task['name'],
                created_at=task['created'], counted_done=task['counted'],
            )
        return found

    def latest(self, source_task_id: str):
        if self.read_error is not None:
            raise self.read_error
        records = self.records.get(source_task_id) or []
        return records[-1] if records else None


def _tracker_phase(task: dict) -> TaskPhase:
    state = task['state']
    if state == 'todo':
        return TaskPhase.WAITING if task['assignee'] else TaskPhase.UNRELEASED
    return {'in_progress': TaskPhase.IN_PROGRESS, 'in_review': TaskPhase.IN_REVIEW,
            'done': TaskPhase.DONE}[state]


class TrackerLauncher(object):
    creates_at_approval = True

    def __init__(self, tracker: FakeTracker, user: str) -> None:
        self.tracker = tracker
        self.user = user
        self.is_available = True

    def available(self):
        return self.is_available

    def prepare(self, request):
        for task_id, task in self.tracker.tasks.items():
            if task['marker'] == request.marker:
                return task_id  # a retried create finds the first
        self.tracker._counter += 1
        task_id = f'PROJ-{self.tracker._counter}'
        self.tracker.tasks[task_id] = {
            'state': 'todo', 'assignee': '', 'name': '', 'created': self.tracker.clock(),
            'counted': False, 'marker': request.marker, 'waits_for': request.waits_for,
        }
        return task_id

    def release(self, request, task_id):
        task = self.tracker.tasks[task_id]
        if not task['assignee']:
            task.update(assignee=self.user, name=self.user.title())
        return task_id

    def withdraw(self, task_id, reason):
        task = self.tracker.tasks.get(task_id)
        if task is not None and not task['assignee']:
            self.tracker.cancelled.append(task_id)

    def counted_done(self, task_id):
        if task_id in self.tracker.counted_errors:
            raise self.tracker.counted_errors.pop(task_id)
        self.tracker.tasks[task_id].update(state='done', counted=True)


class TrackerPublisher(object):
    def __init__(self, tracker: FakeTracker, user: str) -> None:
        self.tracker = tracker
        self.user = user
        self.published = 0

    def publish(self, plan):
        if self.tracker.publish_error is not None:
            raise self.tracker.publish_error
        self.published += 1
        self.tracker.post(plan.source_task_id, parse_record(render_record(plan), author=self.user))


class FakeOwnership(object):
    def __init__(self, identity: str, user: str) -> None:
        self.who = identity
        self.user_handle = user
        self.error: Exception | None = None

    def identity(self):
        if self.error is not None:
            raise self.error
        return self.who

    def user(self):
        return self.user_handle


class FakeHistory(object):
    def __init__(self) -> None:
        self.merged: set[tuple[str, str]] = set()
        self.failing: set[tuple[str, str]] = set()
        self.asked: list[tuple[str, str, float]] = []

    def merged_in_history(self, task_id, repository_id, *, since):
        self.asked.append((task_id, repository_id, since))
        if (task_id, repository_id) in self.failing:
            raise ProbeError('the clone is gone')
        return (task_id, repository_id) in self.merged
