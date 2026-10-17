"""What a host provides so a plan queue can run against its tasks.

The queue itself is generic: propose a split, wait for approval, release each
step when the steps it depends on are done and merged. It knows nothing about
how the host creates or starts a task, where its tracker lives, which agent
proposes the split, or how a change request is found. Those come in through
these interfaces, implemented by the host and handed to ``PlanQueueService``.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Protocol

from plan_queue_core_lib.plan_queue_core_lib.data.plan import Plan


class TaskPhase(str, Enum):
    """Where a step's task stands, as the host sees it."""

    MISSING = 'missing'          # the host has no record of the task at all
    GONE = 'gone'                # it existed and was removed before it was done
    UNRELEASED = 'unreleased'    # created but not handed over to run (e.g. not assigned)
    WAITING = 'waiting'          # released, but not picked up yet
    PREPARING = 'preparing'      # being set up (cloned, branched)
    IN_PROGRESS = 'in_progress'  # being worked on
    IN_REVIEW = 'in_review'      # its change is up for review
    ERRORED = 'errored'          # the run broke
    DONE = 'done'                # the operator marked it done


@dataclass(frozen=True)
class TaskObservation(object):
    phase: TaskPhase
    detail: str = ''
    # Who has the task ('' = nobody): a stable handle and a name to show.
    assignee: str = ''
    assignee_name: str = ''
    created_at: float = 0.0      # when the task was created (0 = unknown)
    # The driver counted it as done without a change request (a shared mark
    # every host can read, e.g. its comment on the task).
    counted_done: bool = False


@dataclass(frozen=True)
class PullRequestFact(object):
    """One change request for a step's branch, as the host found it."""

    state: str                   # 'open' | 'merged' | 'declined'
    url: str = ''
    created_at: str = ''         # ISO-8601; '' when the host does not know


@dataclass(frozen=True)
class LaunchRequest(object):
    """Everything a host needs to create or start one step's task."""

    plan_id: str
    step_id: str
    index: int                   # 1-based position in the plan
    count: int                   # how many steps the plan has
    title: str
    description: str             # composed by the queue (see ``step_text``)
    repositories: tuple[str, ...]
    source_task_id: str
    plan_title: str
    start_mode: str = ''
    model: str = ''
    effort: str = ''
    waits_for: tuple[str, ...] = ()  # the task ids of the steps it waits for

    @property
    def marker(self) -> str:
        """A stable tag for this step, so a retried create can find the first."""
        return f'plan-step:{self.plan_id}/{self.step_id}'


class LaunchRefused(RuntimeError):
    """The host will not start this step for a reason the operator can fix."""


class ProbeError(RuntimeError):
    """The host could not be asked about a step's change requests."""


class TaskLauncher(Protocol):
    """Creates and starts steps' tasks."""

    @property
    def creates_at_approval(self) -> bool:
        """True when every step's task is created at approval (and only
        started at release); False when release both creates and starts it."""

    def available(self) -> bool:
        """May tasks be created / started right now?"""

    def prepare(self, request: LaunchRequest) -> str:
        """Create the step's task without starting it; return its id.

        Called at approval when ``creates_at_approval``. Must find and return
        an already-created task carrying ``request.marker`` rather than create
        a second one. Raise ``LaunchRefused`` or any error to fail the step.
        """

    def release(self, request: LaunchRequest, task_id: str) -> str:
        """Start the step; return the id of the task it runs as.

        ``task_id`` is what ``prepare`` returned (or the task an earlier,
        failed release created). Raise ``LaunchRefused`` for a reason the
        operator can fix, anything else for a failure.
        """

    def withdraw(self, task_id: str, reason: str) -> None:
        """A prepared task will not be started (the plan or step was
        cancelled). Never delete anything; leave a note at most — and leave a
        task someone has taken meanwhile alone."""

    def counted_done(self, task_id: str) -> None:
        """The operator counted the step as done: mark its task done where
        everyone can see it (nothing to do for a host whose tasks only it sees)."""


class TaskProgress(Protocol):
    def observe(self, task_ids: Sequence[str]) -> dict[str, TaskObservation]:
        """Where each released task stands. A missing key means unknown."""


class PullRequestProbe(Protocol):
    def pull_requests(self, task_id: str, repository_id: str) -> list[PullRequestFact]:
        """Every change request of ``task_id``'s branch in that repository,
        into its destination branch. Raise ``ProbeError`` when the host can't
        be asked — never answer [] for "could not ask"."""


class SplitProposer(Protocol):
    def propose(
        self,
        prompt: str,
        *,
        source_task_id: str,
        cancel_event: threading.Event,
        model: str = '',
    ) -> str:
        """Run a read-only agent on ``prompt`` and return its whole reply."""


class SplitConversation(Protocol):
    """The conversation a split is proposed in, when the host proposes it in
    the task's chat: the operator keeps talking to the agent there, and a reply
    that carries a new split replaces the proposal."""

    def latest_reply(self, source_task_id: str, since: float) -> tuple[float, str] | None:
        """``(received_at, text)`` of the chat's newest finished reply after
        ``since`` (epoch seconds), or None. Raise when the chat can't be read."""


class SplitWording(Protocol):
    def wrap_untrusted(self, text: str, *, source: str) -> str:
        """Frame text that came from a file as untrusted content."""

    def guidance(self) -> str:
        """The host's own rules for a good split (may be '')."""


class HistoryProbe(Protocol):
    def merged_in_history(self, task_id: str, repository_id: str, *, since: float) -> bool:
        """Is a commit naming ``task_id`` (created after ``since``) in the
        repository's destination branch? For work merged without a change
        request. Raise ``ProbeError`` when it can't be told."""


class Ownership(Protocol):
    """Who this host is, for plans whose steps several hosts can see."""

    def identity(self) -> str:
        """This host's stable identity. Raise when it can't be told — a host
        that doesn't know who it is never moves shared work."""

    def user(self) -> str:
        """The tracker user this host works as (a stable handle)."""


class PlanPublisher(Protocol):
    def publish(self, plan: Plan) -> None:
        """Write the plan where every host can read it (its record on the
        tracker). Raise to have it retried."""


@dataclass(frozen=True)
class RecordStep(object):
    task_id: str
    step_id: str
    title: str
    waits_for: tuple[str, ...] = ()     # task ids
    repositories: tuple[str, ...] = ()  # where it works ('' = every repository of the plan)
    done: bool = False                  # the driver counts it satisfied


@dataclass(frozen=True)
class PlanRecord(object):
    """A plan as published on the tracker: what another host follows."""

    plan_id: str
    source_task_id: str
    status: str
    driver: str
    driver_user: str
    steps: tuple[RecordStep, ...]
    author: str = ''                  # who posted it (the tracker says)


class PlanRecordSource(Protocol):
    def latest(self, source_task_id: str) -> PlanRecord | None:
        """The newest readable record on ``source_task_id``, its every task a
        sub-task of it (None when there is none). Raise when the tracker can't
        be asked."""


# ``(plan, event)`` — a one-line note of something that happened, for the
# host's log. Called outside the service's lock; must not raise.
PlanObserver = Callable[[Plan, str], None]
