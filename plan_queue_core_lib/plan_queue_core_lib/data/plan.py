"""A plan split into steps, and where each step stands.

A **plan** is one approved piece of work broken into small **steps**. A step
may depend on others; it starts only once every step it depends on is
*satisfied* — its task is done AND its change is merged (or an operator says so).

Everything here is plain data with ``to_dict`` / ``from_dict``. ``from_dict``
tolerates missing and unknown fields, so a plan saved by an older version still
loads, and one with a value it does not recognise falls back to a safe default
instead of failing the whole queue.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class PlanMode(str, Enum):
    """Where the plan's steps live, and who moves them."""

    LOCAL = 'local'          # steps are the host's own tasks; only this host sees them
    TRACKER = 'tracker'      # steps are sub-tasks in a shared tracker; this host drives
    FOLLOWED = 'followed'    # a tracker plan another host drives: shown, never moved here


class PlanStatus(str, Enum):
    PROPOSING = 'proposing'            # a split is being proposed
    PROPOSE_FAILED = 'propose_failed'  # the proposal could not be produced or read
    PROPOSED = 'proposed'              # waiting for the operator to approve / revise
    APPROVED = 'approved'              # steps prepared; nothing released until Run
    RUNNING = 'running'                # releasing steps as they become ready
    PAUSED = 'paused'                  # no new releases; released steps carry on
    FINISHED = 'finished'              # every step satisfied or cancelled
    CANCELLED = 'cancelled'            # stopped by the operator

    @property
    def is_terminal(self) -> bool:
        return self in (PlanStatus.FINISHED, PlanStatus.CANCELLED)

    @property
    def has_steps_in_flight(self) -> bool:
        """States in which steps are tracked, released or waited on."""
        return self in (PlanStatus.APPROVED, PlanStatus.RUNNING, PlanStatus.PAUSED)


class StepStatus(str, Enum):
    BLOCKED = 'blocked'                # a step it depends on is not satisfied yet
    READY = 'ready'                    # may be released
    STARTING = 'starting'              # released; its task has not started work yet
    ACTIVE = 'active'                  # its task is being worked on
    IN_REVIEW = 'in_review'            # its change is up for review
    AWAITING_MERGE = 'awaiting_merge'  # its task is done; waiting to see the merge
    SATISFIED = 'satisfied'            # done and merged — dependents may start
    FAILED = 'failed'                  # needs the operator (see ``reason_code``)
    CANCELLED = 'cancelled'

    @property
    def is_released(self) -> bool:
        """The step's task exists and is running or ran."""
        return self in RELEASED_STATUSES

    @property
    def is_terminal(self) -> bool:
        return self in (StepStatus.SATISFIED, StepStatus.CANCELLED)


RELEASED_STATUSES = frozenset({
    StepStatus.STARTING,
    StepStatus.ACTIVE,
    StepStatus.IN_REVIEW,
    StepStatus.AWAITING_MERGE,
})


class FailReason(str, Enum):
    CREATE_FAILED = 'create_failed'    # its task could not be created at approval
    LAUNCH_REFUSED = 'launch_refused'  # the host refused to start it (a fixable reason)
    LAUNCH_FAILED = 'launch_failed'    # starting it failed
    TASK_GONE = 'task_gone'            # its task disappeared before it was done
    PR_DECLINED = 'pr_declined'        # its change was closed without being merged


class SatisfiedBy(str, Enum):
    MERGED = 'merged'      # done and merged, as observed
    HISTORY = 'history'    # done, no change request, but its commit is in the destination's history
    OPERATOR = 'operator'  # the operator counted it as done
    DRIVER = 'driver'      # the plan's driver says so (a host following a shared plan)


class RepoMergeState(str, Enum):
    MERGED = 'merged'
    OPEN = 'open'
    DECLINED = 'declined'
    NONE = 'none'          # no change request found for this repository
    UNKNOWN = 'unknown'    # the host could not be asked


@dataclass
class RepoMerge(object):
    repository_id: str
    state: RepoMergeState = RepoMergeState.UNKNOWN
    url: str = ''

    def to_dict(self) -> dict[str, Any]:
        return {'repository_id': self.repository_id, 'state': self.state.value, 'url': self.url}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'RepoMerge':
        return cls(
            repository_id=str(data.get('repository_id', '') or ''),
            state=_enum(RepoMergeState, data.get('state'), RepoMergeState.UNKNOWN),
            url=str(data.get('url', '') or ''),
        )


@dataclass
class MergeCheck(object):
    """When the step's change was last looked for, and what was seen."""

    checked_at: float = 0.0
    next_check_at: float = 0.0
    attempts: int = 0
    forced: bool = False         # the operator asked to look now ("Check now")
    repos: list[RepoMerge] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            'checked_at': self.checked_at,
            'next_check_at': self.next_check_at,
            'attempts': self.attempts,
            'forced': self.forced,
            'repos': [repo.to_dict() for repo in self.repos],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> 'MergeCheck':
        data = data or {}
        return cls(
            checked_at=_float(data.get('checked_at')),
            next_check_at=_float(data.get('next_check_at')),
            attempts=max(0, _int(data.get('attempts'))),
            forced=data.get('forced') is True,
            repos=[RepoMerge.from_dict(item) for item in _dicts(data.get('repos'))],
        )


@dataclass
class Step(object):
    id: str
    title: str
    brief: str
    depends_on: list[str] = field(default_factory=list)
    repositories: list[str] = field(default_factory=list)
    status: StepStatus = StepStatus.BLOCKED
    reason_code: str = ''        # a FailReason value while FAILED
    reason: str = ''             # human wording: why it waits or failed
    task_id: str = ''            # the task this step is (or will run as)
    previous_task_ids: list[str] = field(default_factory=list)
    released_at: float = 0.0     # when it was handed over to start
    satisfied_by: str = ''       # a SatisfiedBy value once satisfied
    satisfied_at: float = 0.0
    merge: MergeCheck = field(default_factory=MergeCheck)
    source_file: str = ''        # the split file this step was made from, if any
    created_at: float = 0.0      # when its task was created (the tracker's clock)
    worker: str = ''             # who has its task, by name ('' = nobody yet)
    worker_handle: str = ''      # the same person's stable handle

    @property
    def is_satisfied(self) -> bool:
        return self.status == StepStatus.SATISFIED

    @property
    def prepared_only(self) -> bool:
        """Its task was created up front but never handed over to run."""
        return bool(self.task_id) and not self.released_at and not self.status.is_released

    def to_dict(self) -> dict[str, Any]:
        return {
            'id': self.id,
            'title': self.title,
            'brief': self.brief,
            'depends_on': list(self.depends_on),
            'repositories': list(self.repositories),
            'status': self.status.value,
            'reason_code': self.reason_code,
            'reason': self.reason,
            'task_id': self.task_id,
            'previous_task_ids': list(self.previous_task_ids),
            'released_at': self.released_at,
            'satisfied_by': self.satisfied_by,
            'satisfied_at': self.satisfied_at,
            'merge': self.merge.to_dict(),
            'source_file': self.source_file,
            'created_at': self.created_at,
            'worker': self.worker,
            'worker_handle': self.worker_handle,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'Step':
        return cls(
            id=str(data.get('id', '') or ''),
            title=str(data.get('title', '') or ''),
            brief=str(data.get('brief', '') or ''),
            depends_on=_strings(data.get('depends_on')),
            repositories=_strings(data.get('repositories')),
            status=_enum(StepStatus, data.get('status'), StepStatus.BLOCKED),
            reason_code=str(data.get('reason_code', '') or ''),
            reason=str(data.get('reason', '') or ''),
            task_id=str(data.get('task_id', '') or ''),
            previous_task_ids=_strings(data.get('previous_task_ids')),
            released_at=_float(data.get('released_at')),
            satisfied_by=str(data.get('satisfied_by', '') or ''),
            satisfied_at=_float(data.get('satisfied_at')),
            merge=MergeCheck.from_dict(data.get('merge') if isinstance(data.get('merge'), dict) else None),
            source_file=str(data.get('source_file', '') or ''),
            created_at=_float(data.get('created_at')),
            worker=str(data.get('worker', '') or ''),
            worker_handle=str(data.get('worker_handle', '') or ''),
        )


@dataclass(frozen=True)
class SplitFile(object):
    """One file of a split the operator already wrote: one step per file,
    its text kept exactly as written (it becomes that step's brief)."""

    path: str
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {'path': self.path, 'text': self.text}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'SplitFile':
        return cls(path=str(data.get('path', '') or ''), text=str(data.get('text', '') or ''))


@dataclass
class Revision(object):
    number: int
    feedback: str
    at: float

    def to_dict(self) -> dict[str, Any]:
        return {'number': self.number, 'feedback': self.feedback, 'at': self.at}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'Revision':
        return cls(
            number=_int(data.get('number')),
            feedback=str(data.get('feedback', '') or ''),
            at=_float(data.get('at')),
        )


@dataclass
class Plan(object):
    plan_id: str
    source_task_id: str
    title: str = ''
    source_summary: str = ''
    plan_markdown: str = ''       # a snapshot: the source may be gone by the end
    plan_digest: str = ''
    # A split the operator already wrote as files (a snapshot, like the plan):
    # when set, the proposer only maps them — one step per file, briefs as
    # written — and ``plan_markdown`` is the folder's overview, if it had one.
    split_files: list[SplitFile] = field(default_factory=list)
    repository_ids: list[str] = field(default_factory=list)
    mode: PlanMode = PlanMode.LOCAL
    # The host that releases this plan's steps (TRACKER / FOLLOWED): its own
    # identity and the tracker user it works as. Only the driver moves work.
    driver: str = ''
    driver_user: str = ''
    # What other hosts can read about the plan (the record the host publishes
    # on the tracker) changed in version ``record_version``; the published one
    # is ``published_version``. A failed publish is retried after ``record_retry_at``.
    record_version: int = 0
    published_version: int = 0
    record_retry_at: float = 0.0
    status: PlanStatus = PlanStatus.PROPOSING
    reason: str = ''              # why it failed / was paused, for the operator
    notes: str = ''               # the proposer's notes on its split
    model: str = ''               # the proposer's model ('' = the host default)
    start_mode: str = ''          # how a released step starts ('' = the host default)
    step_model: str = ''          # the model released steps run on ('' = the host default)
    effort: str = ''
    revision: int = 0
    revisions: list[Revision] = field(default_factory=list)
    # The newest chat reply already read for this plan's split (epoch): a
    # later one carrying a split replaces the proposal.
    chat_seen_at: float = 0.0
    steps: list[Step] = field(default_factory=list)
    created_at: float = 0.0
    updated_at: float = 0.0
    approved_at: float = 0.0

    @classmethod
    def new(
        cls,
        source_task_id: str,
        *,
        plan_markdown: str,
        repository_ids,
        now: float,
        title: str = '',
        source_summary: str = '',
        model: str = '',
        split_files=(),
    ) -> 'Plan':
        return cls(
            plan_id=uuid.uuid4().hex,
            source_task_id=str(source_task_id),
            title=title or source_summary or str(source_task_id),
            source_summary=source_summary,
            plan_markdown=plan_markdown,
            plan_digest=hashlib.sha256(plan_markdown.encode('utf-8')).hexdigest(),
            split_files=list(split_files),
            repository_ids=list(repository_ids),
            model=model,
            created_at=now,
            updated_at=now,
        )

    def step(self, step_id: str) -> Step | None:
        for step in self.steps:
            if step.id == step_id:
                return step
        return None

    def index_of(self, step_id: str) -> int:
        for index, step in enumerate(self.steps):
            if step.id == step_id:
                return index
        return -1

    def touch_record(self) -> bool:
        """What other hosts read about this plan changed: publish it again
        (only a shared plan this host drives has a record to publish)."""
        if self.mode == PlanMode.TRACKER:
            self.record_version += 1
        return True

    def step_for_task(self, task_id: str) -> Step | None:
        key = str(task_id or '').strip().lower()
        for step in self.steps:
            if key and step.task_id.lower() == key:
                return step
        return None

    def status_counts(self) -> dict[str, int]:
        counts = {status.value: 0 for status in StepStatus}
        for step in self.steps:
            counts[step.status.value] += 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        return {
            'plan_id': self.plan_id,
            'source_task_id': self.source_task_id,
            'title': self.title,
            'source_summary': self.source_summary,
            'plan_markdown': self.plan_markdown,
            'plan_digest': self.plan_digest,
            'split_files': [split_file.to_dict() for split_file in self.split_files],
            'repository_ids': list(self.repository_ids),
            'mode': self.mode.value,
            'driver': self.driver,
            'driver_user': self.driver_user,
            'record_version': self.record_version,
            'published_version': self.published_version,
            'record_retry_at': self.record_retry_at,
            'status': self.status.value,
            'reason': self.reason,
            'notes': self.notes,
            'model': self.model,
            'start_mode': self.start_mode,
            'step_model': self.step_model,
            'effort': self.effort,
            'revision': self.revision,
            'revisions': [revision.to_dict() for revision in self.revisions],
            'chat_seen_at': self.chat_seen_at,
            'steps': [step.to_dict() for step in self.steps],
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'approved_at': self.approved_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'Plan':
        return cls(
            plan_id=str(data.get('plan_id', '') or ''),
            source_task_id=str(data.get('source_task_id', '') or ''),
            title=str(data.get('title', '') or ''),
            source_summary=str(data.get('source_summary', '') or ''),
            plan_markdown=str(data.get('plan_markdown', '') or ''),
            plan_digest=str(data.get('plan_digest', '') or ''),
            split_files=[SplitFile.from_dict(item) for item in _dicts(data.get('split_files'))],
            repository_ids=_strings(data.get('repository_ids')),
            mode=_enum(PlanMode, data.get('mode'), PlanMode.LOCAL),
            driver=str(data.get('driver', '') or ''),
            driver_user=str(data.get('driver_user', '') or ''),
            record_version=max(0, _int(data.get('record_version'))),
            published_version=max(0, _int(data.get('published_version'))),
            record_retry_at=_float(data.get('record_retry_at')),
            status=_enum(PlanStatus, data.get('status'), PlanStatus.PROPOSE_FAILED),
            reason=str(data.get('reason', '') or ''),
            notes=str(data.get('notes', '') or ''),
            model=str(data.get('model', '') or ''),
            start_mode=str(data.get('start_mode', '') or ''),
            step_model=str(data.get('step_model', '') or ''),
            effort=str(data.get('effort', '') or ''),
            revision=max(0, _int(data.get('revision'))),
            revisions=[Revision.from_dict(item) for item in _dicts(data.get('revisions'))],
            chat_seen_at=_float(data.get('chat_seen_at')),
            steps=[Step.from_dict(item) for item in _dicts(data.get('steps'))],
            created_at=_float(data.get('created_at')),
            updated_at=_float(data.get('updated_at')),
            approved_at=_float(data.get('approved_at')),
        )


def _enum(kind, value, default):
    try:
        return kind(value)
    except ValueError:
        return default


def _float(value) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _strings(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str) and item]


def _dicts(value) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]
