"""The plan queue: what an operator can do with a plan, in one place.

This class only composes the queue's parts — it holds no logic of its own
beyond deciding which action is allowed in which state:

* :mod:`registry`  — plans in memory, every change saved under one lock
* :mod:`proposing` — the agent run that proposes (and revises) a split
* :mod:`graph`     — the single validator for a split
* :mod:`launching` — creating, starting and withdrawing steps' tasks
* :mod:`ticking`   — the background pass: track, merge, unblock, release, finish

Every refusal is a ``PlanQueueError`` whose kind a host maps to a status code.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from typing import Callable

from utils_core_lib.utils_core_lib.tick_thread import TickThread

from plan_queue_core_lib.plan_queue_core_lib.data.plan import (
    FailReason,
    Plan,
    PlanMode,
    PlanStatus,
    Revision,
    SatisfiedBy,
    SplitFile,
    Step,
    StepStatus,
)
from plan_queue_core_lib.plan_queue_core_lib.errors import ErrorKind, PlanQueueError
from plan_queue_core_lib.plan_queue_core_lib.graph import (
    MAX_BRIEF_CHARS,
    MAX_STEPS,
    SplitValidationError,
    StepDraft,
    topological_order,
    validate_split,
)
from plan_queue_core_lib.plan_queue_core_lib.launching import (
    build_request,
    count_done,
    prepare_task,
    withdraw_task,
)
from plan_queue_core_lib.plan_queue_core_lib.ports import (
    HistoryProbe,
    Ownership,
    PlanObserver,
    PlanPublisher,
    PlanRecord,
    PlanRecordSource,
    PullRequestProbe,
    SplitConversation,
    SplitProposer,
    SplitWording,
    TaskLauncher,
    TaskProgress,
)
from plan_queue_core_lib.plan_queue_core_lib.proposing import ProposalWorker
from plan_queue_core_lib.plan_queue_core_lib.registry import PlanRegistry
from plan_queue_core_lib.plan_queue_core_lib.release import blocked_reason, initial_status, waiting_on
from plan_queue_core_lib.plan_queue_core_lib.store import PlanQueueStore
from plan_queue_core_lib.plan_queue_core_lib.plan_record import acceptable
from plan_queue_core_lib.plan_queue_core_lib.ticking import PlanTicker, apply_record, as_record

DEFAULT_TICK_SECONDS = 5.0
MAX_PLAN_CHARS = 200_000
MAX_FEEDBACK_CHARS = 8000


class PlanQueueService(object):
    def __init__(
        self,
        *,
        store: PlanQueueStore,
        proposer: SplitProposer,
        wording: SplitWording,
        progress: TaskProgress,
        probe: PullRequestProbe,
        observer: PlanObserver | None = None,
        can_propose: Callable[[str], str] | None = None,
        clock: Callable[[], float] = time.time,
        tick_seconds: float = DEFAULT_TICK_SECONDS,
        logger: logging.Logger | None = None,
        mode_for: Callable[[Plan], PlanMode] | None = None,
        ownership: Ownership | None = None,
        publisher: PlanPublisher | None = None,
        record_source: PlanRecordSource | None = None,
        history: HistoryProbe | None = None,
        conversation: SplitConversation | None = None,
    ) -> None:
        # ``source_task_id -> refusal reason``, '' when the host can propose.
        self._can_propose = can_propose
        self._clock = clock
        self._observer = observer
        self._logger = logger or logging.getLogger('PlanQueueService')
        # Where an approved plan's steps live (LOCAL unless the host says a
        # shared tracker can hold them), and one launcher per kind.
        self._mode_for = mode_for
        self._ownership = ownership
        self._record_source = record_source
        self._launchers: dict[PlanMode, TaskLauncher] = {}
        self._registry = PlanRegistry(store, clock=clock)
        self._proposals = ProposalWorker(
            self._registry, proposer, wording, observer=observer, logger=self._logger, clock=clock,
        )
        self._ticker = PlanTicker(
            self._registry, progress=progress, probe=probe, launcher=self._launcher_for,
            clock=clock, observer=observer, logger=self._logger, ownership=ownership,
            publisher=publisher, record_source=record_source, history=history,
            conversation=conversation,
        )
        self._thread = TickThread(
            self._ticker.tick, interval_seconds=tick_seconds,
            name='plan-queue-tick', logger=self._logger,
        )

    # ----- wiring and lifecycle -----

    def bind_launcher(self, launcher: TaskLauncher | None, *, mode: PlanMode = PlanMode.LOCAL) -> None:
        """The host's launcher for plans of ``mode``; nothing of that kind is
        created or started until one is bound."""
        if launcher is None:
            self._launchers.pop(mode, None)
        else:
            self._launchers[mode] = launcher

    def start(self) -> bool:
        return self._thread.start()

    def tick(self) -> None:
        """One pass now (the background thread does this every few seconds)."""
        self._ticker.tick()

    def shutdown(self) -> None:
        self._thread.stop()
        self._proposals.shutdown()

    def mark_interrupted(self, reason: str, *, proposal_reason: str = '') -> None:
        """After a restart: nothing that was in motion carries on unasked.

        A proposal in progress failed (``proposal_reason``, else ``reason``);
        a running plan is paused (``reason`` — the operator presses Run again);
        a step claimed for release that never got a task goes back to ready.
        """
        for plan in self._registry.all():
            if plan.mode == PlanMode.FOLLOWED:
                continue  # another host drives it: nothing here was in motion

            def apply(live: Plan) -> bool:
                if live.status == PlanStatus.PROPOSING:
                    live.status = PlanStatus.PROPOSE_FAILED
                    live.reason = proposal_reason or reason
                elif live.status == PlanStatus.RUNNING:
                    live.status = PlanStatus.PAUSED
                    live.reason = reason
                for step in live.steps:
                    if step.status == StepStatus.STARTING and not step.task_id:
                        step.status = StepStatus.READY
                return True

            self._registry.mutate(plan.plan_id, apply)

    # ----- reading -----

    def get(self, plan_id: str) -> Plan:
        return self._registry.get(plan_id)

    def plans(self) -> list[Plan]:
        return self._registry.all()

    def plan_for_source(self, source_task_id: str) -> Plan | None:
        """The newest plan split from ``source_task_id`` (any state)."""
        key = str(source_task_id).strip().lower()
        matching = [plan for plan in self._registry.all() if plan.source_task_id.lower() == key]
        return matching[-1] if matching else None

    def plan_for_task(self, task_id: str) -> Plan | None:
        """The newest plan one of whose steps runs as ``task_id``."""
        matching = [plan for plan in self._registry.all() if plan.step_for_task(task_id) is not None]
        return matching[-1] if matching else None

    def waiting_for(self, task_id: str) -> list[str] | None:
        """What the step running as ``task_id`` still waits for (its parents'
        task ids, or step ids for parents with no task yet) — [] when it may
        start, None when no plan here knows the task."""
        plan = self.plan_for_task(task_id)
        if plan is None:
            return None
        step = plan.step_for_task(task_id)
        return [plan.step(parent).task_id or parent for parent in waiting_on(plan, step)]

    def follow_source(self, source_task_id: str, *, title: str = '') -> Plan | None:
        """Follow the plan published on ``source_task_id`` (None when there is
        none). Raises ``PlanQueueError`` when it can't be read or trusted."""
        if self._record_source is None:
            return None
        try:
            record = self._record_source.latest(source_task_id)
        except Exception as exc:
            raise PlanQueueError(
                f'the plan on {source_task_id} could not be read: {exc}', ErrorKind.UNAVAILABLE,
            ) from exc
        if record is None:
            return None
        return self.follow(record, title=title)

    def summaries(self) -> list[dict]:
        return [
            {
                'plan_id': plan.plan_id,
                'source_task_id': plan.source_task_id,
                'title': plan.title,
                'status': plan.status.value,
                'reason': plan.reason,
                'mode': plan.mode.value,
                'driver_user': plan.driver_user,
                'step_count': len(plan.steps),
                'counts': plan.status_counts(),
                'task_ids': [step.task_id for step in plan.steps if step.task_id],
                'updated_at': plan.updated_at,
            }
            for plan in self._registry.all()
        ]

    def live_by_source(self) -> dict[str, dict]:
        """``source task id -> its live plan`` (the newest neither finished nor
        cancelled) — a few fields, small enough for a list polled every few
        seconds, so a task's own tab can show it has a plan running."""
        live = {}
        for plan in self._registry.all():  # oldest first: the newest wins
            if not plan.status.is_terminal:
                live[plan.source_task_id] = {
                    'plan_id': plan.plan_id,
                    'status': plan.status.value,
                    'step_count': len(plan.steps),
                    'counts': plan.status_counts(),
                }
        return live

    def live_task_codes(self) -> set[str]:
        """Every task code a live plan still owns, lower-cased: the task the
        plan was split from, and the task of each step not yet done or
        cancelled (a failed step keeps its ticket, so it stays).

        The queue knows its tasks by CODE, whatever a tracker says about a
        ticket's assignee. A host asking "is this task still wanted?" should
        ask here too: a step held unassigned (that is how the queue holds one)
        or handed back reads, to a check that only looks at assignees, exactly
        like a ticket nobody wants any more."""
        codes: set[str] = set()
        for plan in self._registry.all():
            if plan.status.is_terminal:
                continue
            codes.add(plan.source_task_id.strip().lower())
            codes.update(
                step.task_id.strip().lower()
                for step in plan.steps
                if step.task_id and not step.status.is_terminal
            )
        codes.discard('')
        return codes

    # ----- proposing -----

    def propose(
        self,
        source_task_id: str,
        *,
        plan_markdown: str,
        repository_ids: Sequence[str],
        title: str = '',
        source_summary: str = '',
        model: str = '',
        split_files: Sequence[SplitFile] = (),
    ) -> Plan:
        """Start proposing a split of ``plan_markdown`` — or, with
        ``split_files``, a mapping of the split the operator already wrote (one
        step per file; ``plan_markdown`` is then the split's overview, if any)."""
        text = str(plan_markdown or '').strip()
        files = _checked_split_files(split_files)
        if not text and not files:
            raise PlanQueueError('the task has no plan to split', ErrorKind.INVALID)
        if len(text) > MAX_PLAN_CHARS:
            raise PlanQueueError('the plan is too large to split', ErrorKind.INVALID)
        repositories = [str(repo) for repo in repository_ids if str(repo)]
        if not repositories:
            raise PlanQueueError('the task has no repositories', ErrorKind.INVALID)
        refusal = self._can_propose(source_task_id) if self._can_propose is not None else ''
        if refusal:
            raise PlanQueueError(refusal)
        current = self.plan_for_source(source_task_id)
        if current is not None and not current.status.is_terminal:
            raise PlanQueueError(
                f'{source_task_id} already has a plan in progress ({current.status.value})',
            )
        plan = Plan.new(
            source_task_id, plan_markdown=text, repository_ids=repositories,
            now=self._clock(), title=title, source_summary=source_summary, model=model,
            split_files=files,
        )
        plan = self._registry.add(plan)
        self._proposals.start(plan)
        return plan

    def revise(self, plan_id: str, feedback: str) -> Plan:
        text = str(feedback or '').strip()
        if not text:
            raise PlanQueueError('say what to change', ErrorKind.INVALID)
        if len(text) > MAX_FEEDBACK_CHARS:
            raise PlanQueueError('the feedback is too long', ErrorKind.INVALID)

        def apply(plan: Plan) -> None:
            _require(plan, PlanStatus.PROPOSED, PlanStatus.PROPOSE_FAILED)
            plan.revision += 1
            plan.revisions.append(Revision(plan.revision, text, self._clock()))
            plan.status = PlanStatus.PROPOSING
            plan.reason = ''

        self._registry.mutate(plan_id, apply)
        plan = self._registry.get(plan_id)
        self._proposals.start(plan, feedback=text)
        return plan

    def retry_proposal(self, plan_id: str) -> Plan:
        """Propose again from scratch after a failed proposal."""
        def apply(plan: Plan) -> None:
            _require(plan, PlanStatus.PROPOSE_FAILED)
            plan.revision += 1
            plan.status = PlanStatus.PROPOSING
            plan.reason = ''

        self._registry.mutate(plan_id, apply)
        plan = self._registry.get(plan_id)
        self._proposals.start(plan)
        return plan

    def edit(
        self, plan_id: str, drafts: Sequence[StepDraft], *, title: str | None = None,
        repositories: Sequence[str] = (),
    ) -> Plan:
        """The operator's edit of a proposed split, checked by the same validator.

        ``repositories`` are more repositories the plan's tasks may work in (one
        the operator added — e.g. a shared library every task builds on).
        """
        plan = self._registry.get(plan_id)
        allowed = _merged(plan.repository_ids, repositories)
        try:
            steps = validate_split(list(drafts), allowed_repositories=allowed)
        except SplitValidationError as exc:
            raise PlanQueueError(str(exc), ErrorKind.INVALID) from exc

        def apply(live: Plan) -> None:
            _require(live, PlanStatus.PROPOSED)
            live.repository_ids = _merged(live.repository_ids, repositories)
            sources = {step.id: step.source_file for step in live.steps}
            live.steps = [
                Step(id=d.id, title=d.title, brief=d.brief,
                     depends_on=list(d.depends_on), repositories=list(d.repositories),
                     source_file=sources.get(d.id, ''))
                for d in steps
            ]
            if title is not None and title.strip():
                live.title = title.strip()

        self._registry.mutate(plan_id, apply)
        return self._registry.get(plan_id)

    # ----- approving and running -----

    def can_share(self, plan_id: str) -> bool:
        """Could this plan's steps live on a shared tracker (the host says)?"""
        return self._possible_mode(self._registry.get(plan_id)) == PlanMode.TRACKER

    def approve(
        self, plan_id: str, *, start_mode: str = '', model: str = '', effort: str = '',
        run: bool = False, shared: bool | None = None,
    ) -> Plan:
        """Accept the split: create the steps' tasks (if the host does that
        up front), then wait for Run — or start at once with ``run``.

        ``shared`` is the operator's choice: True puts the steps on the shared
        tracker (this host becomes the plan's driver — the one that releases),
        False keeps them the host's own; None leaves it to the host.
        """
        mode = self._chosen_mode(self._registry.get(plan_id), shared)
        launcher = self._require_launcher(mode)
        driver, driver_user = self._whoami() if mode == PlanMode.TRACKER else ('', '')

        def apply(plan: Plan) -> None:
            _require(plan, PlanStatus.PROPOSED)
            if not plan.steps:
                raise PlanQueueError('the split has no steps', ErrorKind.INVALID)
            plan.mode = mode
            plan.driver = driver
            plan.driver_user = driver_user
            plan.status = PlanStatus.APPROVED
            plan.approved_at = self._clock()
            plan.reason = ''
            plan.start_mode = start_mode
            plan.step_model = model
            plan.effort = effort
            for step in plan.steps:
                step.status = initial_status(plan, step)
                step.reason = blocked_reason(plan, step)

        self._registry.mutate(plan_id, apply)
        self._note(plan_id, 'approved')
        if launcher.creates_at_approval:
            self._prepare_all(plan_id, launcher)
        self._registry.mutate(plan_id, lambda live: live.touch_record())
        if run:
            return self.run(plan_id)
        return self._registry.get(plan_id)

    def run(self, plan_id: str) -> Plan:
        current = self._registry.get(plan_id)
        _require_driven_here(current)
        self._require_launcher(current.mode)

        def apply(plan: Plan) -> None:
            _require_driven_here(plan)
            _require(plan, PlanStatus.APPROVED, PlanStatus.PAUSED)
            plan.status = PlanStatus.RUNNING
            plan.reason = ''
            plan.touch_record()

        self._registry.mutate(plan_id, apply)
        self._note(plan_id, 'running')
        return self._registry.get(plan_id)

    def pause(self, plan_id: str) -> Plan:
        def apply(plan: Plan) -> None:
            _require_driven_here(plan)
            _require(plan, PlanStatus.RUNNING)
            plan.status = PlanStatus.PAUSED
            plan.reason = 'paused by the operator'
            plan.touch_record()

        self._registry.mutate(plan_id, apply)
        return self._registry.get(plan_id)

    def stop(self, plan_id: str) -> Plan:
        """Cancel the plan. Steps already handed over keep running — their
        tasks are ordinary tasks; only what never started is cancelled."""
        def apply(plan: Plan) -> list[str]:
            _require_driven_here(plan)
            if plan.status.is_terminal:
                raise PlanQueueError(f'the plan is already {plan.status.value}')
            plan.status = PlanStatus.CANCELLED
            plan.reason = 'stopped by the operator'
            plan.touch_record()
            return _cancel_unreleased(plan, 'the plan was stopped')

        withdrawn = self._registry.mutate(plan_id, apply)
        self._proposals.cancel(plan_id)
        self._withdraw(plan_id, withdrawn, 'the plan was stopped')
        return self._registry.get(plan_id)

    # ----- a plan another host drives -----

    def follow(self, record: PlanRecord, *, title: str = '') -> Plan:
        """Show a plan another host drives, from its record on the tracker:
        read-only here until taken over. Following it again refreshes it."""
        existing = self._registry.find(record.plan_id)
        if existing is not None:
            problem = acceptable(record, previous=as_record(existing))
            if problem:
                raise PlanQueueError(f'the plan record cannot be followed: {problem}')
            me = self._ticker.identity()
            self._registry.mutate(record.plan_id, lambda live: apply_record(live, record, me) or True)
            return self._registry.get(record.plan_id)
        problem = acceptable(record, previous=None)
        if problem:
            raise PlanQueueError(f'the plan record cannot be followed: {problem}')
        plan = _followed_plan(record, title=title, now=self._clock())
        return self._registry.add(plan)

    def take_over(self, plan_id: str) -> Plan:
        """Drive a plan another host drove: from now on this host releases its
        steps (to the tracker user it works as). It starts paused — Run starts it."""
        if self._registry.get(plan_id).mode != PlanMode.FOLLOWED:
            raise PlanQueueError('this plan is not driven by another host')
        self._require_launcher(PlanMode.TRACKER)
        driver, driver_user = self._whoami()

        def apply(plan: Plan) -> None:
            if plan.status.is_terminal:
                raise PlanQueueError(f'the plan is {plan.status.value}')
            plan.mode = PlanMode.TRACKER
            plan.driver = driver
            plan.driver_user = driver_user
            plan.status = PlanStatus.PAUSED if plan.status != PlanStatus.APPROVED else PlanStatus.APPROVED
            plan.reason = 'taken over — press Run to start releasing'
            plan.touch_record()

        self._registry.mutate(plan_id, apply)
        self._note(plan_id, f'taken over by {driver_user}')
        return self._registry.get(plan_id)

    def discard(self, plan_id: str) -> None:
        """Forget a plan that is not running. Nothing is deleted from the host."""
        plan = self._registry.get(plan_id)
        if plan.status == PlanStatus.RUNNING and plan.mode != PlanMode.FOLLOWED:
            raise PlanQueueError('pause or stop the plan before discarding it')
        prepared = [
            step.task_id for step in plan.steps
            if step.prepared_only and not step.status.is_terminal
        ] if plan.mode != PlanMode.FOLLOWED else []
        launcher = self._launcher_for(plan)
        self._proposals.cancel(plan_id)
        self._registry.remove(plan_id)
        for task_id in prepared:
            withdraw_task(launcher, task_id, 'the plan was discarded', self._logger)

    # ----- one step -----

    def mark_satisfied(self, plan_id: str, step_id: str) -> Plan:
        """Count a step as done: its dependents may start. On a shared tracker
        its task is marked done there first — for every host, not just here."""
        plan = self._registry.get(plan_id)
        _require_driven_here(plan)
        if plan.mode == PlanMode.TRACKER:
            task_id = _step(plan, step_id).task_id
            try:
                count_done(self._launcher_for(plan), task_id)
            except Exception as exc:
                raise PlanQueueError(
                    f'could not mark {task_id} done: {exc}', ErrorKind.UNAVAILABLE,
                ) from exc

        def apply(plan: Plan) -> list[str]:
            _require_in_flight(plan)
            step = _step(plan, step_id)
            if step.status.is_terminal:
                raise PlanQueueError(f'step {step_id} is already {step.status.value}')
            withdrawn = [step.task_id] if step.prepared_only and plan.mode == PlanMode.LOCAL else []
            step.status = StepStatus.SATISFIED
            step.satisfied_by = SatisfiedBy.OPERATOR.value
            step.satisfied_at = self._clock()
            step.reason = 'counted as done by the operator'
            step.reason_code = ''
            plan.touch_record()
            return withdrawn

        withdrawn = self._registry.mutate(plan_id, apply)
        self._withdraw(plan_id, withdrawn, 'counted as done without running')
        return self._registry.get(plan_id)

    def cancel_step(self, plan_id: str, step_id: str) -> Plan:
        """Stop tracking a step. A task already handed over is left alone."""
        def apply(plan: Plan) -> list[str]:
            _require_driven_here(plan)
            _require_in_flight(plan)
            step = _step(plan, step_id)
            if step.status.is_terminal:
                raise PlanQueueError(f'step {step_id} is already {step.status.value}')
            withdrawn = [step.task_id] if step.prepared_only else []
            step.status = StepStatus.CANCELLED
            step.reason = 'cancelled by the operator'
            return withdrawn

        withdrawn = self._registry.mutate(plan_id, apply)
        self._withdraw(plan_id, withdrawn, 'the step was cancelled')
        return self._registry.get(plan_id)

    def retry_step(self, plan_id: str, step_id: str) -> Plan:
        """Try a failed step again, the way its failure calls for."""
        launcher = self._launcher_for(self._registry.get(plan_id))

        def apply(plan: Plan) -> bool:
            _require_driven_here(plan)
            _require_in_flight(plan)
            step = _step(plan, step_id)
            if step.status != StepStatus.FAILED:
                raise PlanQueueError(f'step {step_id} has not failed')
            code = step.reason_code
            step.reason_code = ''
            step.reason = ''
            if code == FailReason.PR_DECLINED.value:
                step.status = StepStatus.AWAITING_MERGE
                step.merge.attempts = 0
                step.merge.next_check_at = 0.0
                return False
            if code in (FailReason.CREATE_FAILED.value, FailReason.TASK_GONE.value) and step.task_id:
                step.previous_task_ids.append(step.task_id)
                step.task_id = ''
            step.status = initial_status(plan, step)
            step.reason = blocked_reason(plan, step)
            return bool(launcher is not None and launcher.creates_at_approval and not step.task_id)

        needs_task = self._registry.mutate(plan_id, apply)
        if needs_task:
            self._prepare_one(plan_id, step_id, launcher)
            self._registry.mutate(plan_id, lambda live: live.touch_record())
        return self._registry.get(plan_id)

    def check_now(self, plan_id: str, step_id: str) -> Plan:
        """Look for the step's merge on the next tick, whatever the backoff."""
        def apply(plan: Plan) -> None:
            step = _step(plan, step_id)
            if not step.status.is_released:
                raise PlanQueueError(f'step {step_id} has not started')
            step.merge.forced = True
            step.merge.next_check_at = 0.0

        self._registry.mutate(plan_id, apply)
        return self._registry.get(plan_id)

    # ----- helpers -----

    def _possible_mode(self, plan: Plan) -> PlanMode:
        return self._mode_for(plan) if self._mode_for is not None else PlanMode.LOCAL

    def _chosen_mode(self, plan: Plan, shared: bool | None) -> PlanMode:
        possible = self._possible_mode(plan)
        if shared is None:
            return possible
        if not shared:
            return PlanMode.LOCAL
        if possible != PlanMode.TRACKER:
            raise PlanQueueError(
                f'the tracker of {plan.source_task_id} cannot hold its steps as sub-tasks', ErrorKind.INVALID,
            )
        return PlanMode.TRACKER

    def _launcher_for(self, plan: Plan) -> TaskLauncher | None:
        if plan.mode == PlanMode.FOLLOWED:
            return None
        return self._launchers.get(plan.mode)

    def _whoami(self) -> tuple[str, str]:
        """This host's identity and tracker user — the driver of a shared plan."""
        if self._ownership is None:
            raise PlanQueueError('this host cannot drive a shared plan', ErrorKind.UNAVAILABLE)
        try:
            return str(self._ownership.identity()), str(self._ownership.user())
        except Exception as exc:
            raise PlanQueueError(
                f'cannot tell which tracker user this host is: {exc}', ErrorKind.UNAVAILABLE,
            ) from exc

    def _require_launcher(self, mode: PlanMode = PlanMode.LOCAL) -> TaskLauncher:
        launcher = self._launchers.get(mode)
        if launcher is None:
            raise PlanQueueError('tasks cannot be created yet', ErrorKind.UNAVAILABLE)
        try:
            available = launcher.available()
        except Exception:
            available = False
        if not available:
            raise PlanQueueError('tasks cannot be created right now', ErrorKind.UNAVAILABLE)
        return launcher

    def _prepare_all(self, plan_id: str, launcher: TaskLauncher) -> None:
        """Create every step's task, parents first (so a child's description
        can name its parents' task ids)."""
        plan = self._registry.get(plan_id)
        for step_id in topological_order(plan.steps):
            self._prepare_one(plan_id, step_id, launcher)

    def _prepare_one(self, plan_id: str, step_id: str, launcher: TaskLauncher) -> None:
        plan = self._registry.get(plan_id)
        step = plan.step(step_id)
        if step is None or step.task_id or step.status.is_terminal:
            return
        outcome = prepare_task(launcher, build_request(plan, step))

        def apply(live: Plan) -> None:
            target = live.step(step_id)
            if target is None or target.task_id or target.status.is_terminal:
                return
            if outcome.ok:
                target.task_id = outcome.task_id
            else:
                target.status = StepStatus.FAILED
                target.reason_code = outcome.fail_reason.value
                target.reason = outcome.message

        self._registry.mutate(plan_id, apply)
        if outcome.ok:
            self._note(plan_id, f'created {outcome.task_id} for step {step_id}')
        else:
            self._note(plan_id, f'could not create step {step_id}: {outcome.message}')

    def _withdraw(self, plan_id: str, task_ids: list[str], reason: str) -> None:
        launcher = self._launcher_for(self._registry.get(plan_id))
        for task_id in task_ids:
            withdraw_task(launcher, task_id, reason, self._logger)

    def _note(self, plan_id: str, event: str) -> None:
        if self._observer is None:
            return
        plan = self._registry.find(plan_id)
        if plan is None:
            return
        try:
            self._observer(plan, event)
        except Exception:
            self._logger.exception('the plan observer failed')


def _require(plan: Plan, *statuses: PlanStatus) -> None:
    if plan.status not in statuses:
        allowed = ' or '.join(status.value for status in statuses)
        raise PlanQueueError(f'the plan is {plan.status.value}; this needs it {allowed}')


def _merged(repositories: Sequence[str], more: Sequence[str]) -> list[str]:
    """``repositories`` then each new one of ``more``, in order, no blanks."""
    merged = list(repositories)
    for repo in (str(item).strip() for item in more):
        if repo and repo not in merged:
            merged.append(repo)
    return merged


def _require_driven_here(plan: Plan) -> None:
    if plan.mode == PlanMode.FOLLOWED:
        raise PlanQueueError(
            f'this plan is driven by {plan.driver_user or "another host"}; take it over to change it',
        )


def _followed_plan(record: PlanRecord, *, title: str, now: float) -> Plan:
    step_of = {entry.task_id: entry.step_id for entry in record.steps}
    plan = Plan(
        plan_id=record.plan_id,
        source_task_id=record.source_task_id,
        title=title or record.source_task_id,
        mode=PlanMode.FOLLOWED,
        status=PlanStatus.APPROVED,
        driver=record.driver,
        driver_user=record.driver_user,
        steps=[
            Step(id=entry.step_id, title=entry.title, brief='', task_id=entry.task_id,
                 depends_on=[step_of[task] for task in entry.waits_for])
            for entry in record.steps
        ],
        created_at=now,
        updated_at=now,
    )
    apply_record(plan, record, '')  # what the driver says is done, first …
    for step in plan.steps:
        if not step.is_satisfied:  # … so the rest start where that leaves them
            step.status = initial_status(plan, step)
            step.reason = blocked_reason(plan, step)
    return plan


def _require_in_flight(plan: Plan) -> None:
    if not plan.status.has_steps_in_flight:
        raise PlanQueueError(f'the plan is {plan.status.value}')


def _step(plan: Plan, step_id: str) -> Step:
    step = plan.step(step_id)
    if step is None:
        raise PlanQueueError(f'the plan has no step {step_id}', ErrorKind.NOT_FOUND)
    return step


def _checked_split_files(split_files: Sequence[SplitFile]) -> list[SplitFile]:
    """An operator's split, refused up front if it can't become steps: every
    file becomes one step whose brief is the file, so the step rules apply."""
    files = [SplitFile(str(item.path).strip(), str(item.text).strip()) for item in split_files]
    if len(files) > MAX_STEPS:
        raise PlanQueueError(
            f'the split has {len(files)} files; at most {MAX_STEPS} tasks are allowed',
            ErrorKind.INVALID,
        )
    seen: set[str] = set()
    for item in files:
        if not item.path or item.path in seen:
            raise PlanQueueError(f'the split names the file {item.path!r} twice or not at all', ErrorKind.INVALID)
        seen.add(item.path)
        if not item.text:
            raise PlanQueueError(f'{item.path} is empty', ErrorKind.INVALID)
        if len(item.text) > MAX_BRIEF_CHARS:
            raise PlanQueueError(
                f'{item.path} is {len(item.text)} characters; a task\'s text may be at most '
                f'{MAX_BRIEF_CHARS}', ErrorKind.INVALID,
            )
    return files


def _cancel_unreleased(plan: Plan, reason: str) -> list[str]:
    """Cancel every step that never started; return the prepared task ids."""
    withdrawn = []
    for step in plan.steps:
        if step.status.is_released or step.status.is_terminal:
            continue
        if step.prepared_only:
            withdrawn.append(step.task_id)
        step.status = StepStatus.CANCELLED
        step.reason = reason
    return withdrawn
