"""Ask an agent to split a plan, read its answer, and record the result.

One proposal runs per plan, on its own thread (an agent run takes minutes). A
reply that can't be used is shown to the agent once more with the exact reason
("step 'ui' depends on unknown step 'api2'"), so a slip is repaired without
the operator. Whatever happens, the plan ends up PROPOSED or PROPOSE_FAILED —
unless it was cancelled or revised meanwhile, in which case the result is
dropped: only the proposal the plan is still waiting for may land.
"""

from __future__ import annotations

import logging
import threading
import time

from plan_queue_core_lib.plan_queue_core_lib.data.plan import Plan, PlanStatus, Step
from plan_queue_core_lib.plan_queue_core_lib.errors import ErrorKind, PlanQueueError
from plan_queue_core_lib.plan_queue_core_lib.graph import StepDraft
from plan_queue_core_lib.plan_queue_core_lib.ports import PlanObserver, SplitProposer, SplitWording
from plan_queue_core_lib.plan_queue_core_lib.proposer_prompt import build_files_prompt, build_propose_prompt
from plan_queue_core_lib.plan_queue_core_lib.registry import PlanRegistry
from plan_queue_core_lib.plan_queue_core_lib.split import (
    SplitProposal,
    SplitProposalError,
    parse_task_split,
)

REPAIR_ATTEMPTS = 1
# How long shutdown waits for proposals to notice they were cancelled.
SHUTDOWN_WAIT_SECONDS = 5.0


class ProposalWorker(object):
    def __init__(
        self,
        registry: PlanRegistry,
        proposer: SplitProposer,
        wording: SplitWording,
        *,
        observer: PlanObserver | None = None,
        logger: logging.Logger | None = None,
        clock=time.time,
    ) -> None:
        self._clock = clock
        self._registry = registry
        self._proposer = proposer
        self._wording = wording
        self._observer = observer
        self._logger = logger or logging.getLogger('ProposalWorker')
        self._lock = threading.Lock()
        self._cancels: dict[str, threading.Event] = {}
        self._threads: dict[str, threading.Thread] = {}

    def start(self, plan: Plan, *, feedback: str = '') -> None:
        """Propose a split for ``plan`` (already PROPOSING) in the background."""
        cancel = threading.Event()
        with self._lock:
            previous = self._cancels.get(plan.plan_id)
            if previous is not None:
                previous.set()
            self._cancels[plan.plan_id] = cancel
            thread = threading.Thread(
                target=self._run, args=(plan, feedback, cancel),
                name=f'plan-split-{plan.plan_id[:8]}', daemon=True,
            )
            self._threads[plan.plan_id] = thread
        thread.start()

    def cancel(self, plan_id: str) -> None:
        with self._lock:
            cancel = self._cancels.get(plan_id)
        if cancel is not None:
            cancel.set()

    def shutdown(self, timeout: float = SHUTDOWN_WAIT_SECONDS) -> None:
        """Cancel every proposal and wait (briefly) for their threads, so
        nothing is written after shutdown returns."""
        with self._lock:
            for cancel in self._cancels.values():
                cancel.set()
            threads = list(self._threads.values())
        deadline = time.monotonic() + timeout
        for thread in threads:
            thread.join(max(0.0, deadline - time.monotonic()))

    def join(self, plan_id: str, timeout: float | None = None) -> None:
        """Wait for a plan's proposal to finish (tests, shutdown)."""
        with self._lock:
            thread = self._threads.get(plan_id)
        if thread is not None:
            thread.join(timeout)

    # ----- the run -----

    def _run(self, plan: Plan, feedback: str, cancel: threading.Event) -> None:
        try:
            proposal, error = self._propose(plan, feedback, cancel)
        except Exception as exc:  # never let the thread die silently
            self._logger.exception('proposing a split for plan %s failed', plan.plan_id)
            proposal, error = None, f'the proposal failed: {exc}'
        finally:
            with self._lock:
                if self._cancels.get(plan.plan_id) is cancel:
                    del self._cancels[plan.plan_id]
        if not cancel.is_set():
            self._record(plan, proposal, error)
        with self._lock:
            if self._threads.get(plan.plan_id) is threading.current_thread():
                del self._threads[plan.plan_id]

    def _propose(
        self, plan: Plan, feedback: str, cancel: threading.Event,
    ) -> tuple[SplitProposal | None, str]:
        problem = ''
        files = files_of(plan)
        for _attempt in range(1 + REPAIR_ATTEMPTS):
            prompt = self._prompt(plan, feedback, problem)
            try:
                reply = self._proposer.propose(
                    prompt, source_task_id=plan.source_task_id,
                    cancel_event=cancel, model=plan.model,
                )
            except Exception as exc:
                return None, f'the proposer failed: {exc}'
            if cancel.is_set():
                return None, ''
            self._registry.store.save_reply(plan.plan_id, reply or '')
            try:
                return parse_task_split(reply, allowed_repositories=plan.repository_ids, files=files), ''
            except SplitProposalError as exc:
                problem = str(exc)
        return None, f'the proposal could not be used: {problem}'

    def _prompt(self, plan: Plan, feedback: str, problem: str) -> str:
        """Split the plan — or, when the operator already split it into files,
        map those files (one step each, briefs as written)."""
        wrap = self._wording.wrap_untrusted
        if plan.split_files:
            overview = plan.plan_markdown.strip()
            return build_files_prompt(
                plan_title=plan.title,
                wrapped_files=[
                    (split_file.path, wrap(split_file.text, source=split_file.path))
                    for split_file in plan.split_files
                ],
                wrapped_overview=wrap(overview, source=f'overview of {plan.source_task_id}') if overview else '',
                repositories=plan.repository_ids,
                guidance=self._wording.guidance(),
                previous_steps=[(draft, step.source_file) for draft, step in zip(_drafts(plan.steps), plan.steps)]
                if feedback else (),
                feedback=feedback,
                problem=problem,
            )
        return build_propose_prompt(
            plan_title=plan.title,
            wrapped_plan=wrap(plan.plan_markdown, source=f'plan of {plan.source_task_id}'),
            repositories=plan.repository_ids,
            guidance=self._wording.guidance(),
            previous_steps=_drafts(plan.steps) if feedback else (),
            feedback=feedback,
            problem=problem,
        )

    def _record(self, started: Plan, proposal: SplitProposal | None, error: str) -> None:
        answered_at = self._clock()

        def apply(plan: Plan) -> bool:
            # Only the proposal this plan is still waiting for may land.
            if plan.status != PlanStatus.PROPOSING or plan.revision != started.revision:
                return False
            # Replies up to now are this proposal's own; a later one is the
            # operator steering the split in the chat.
            plan.chat_seen_at = max(plan.chat_seen_at, answered_at)
            if proposal is None:
                plan.status = PlanStatus.PROPOSE_FAILED
                plan.reason = error or 'the proposal failed'
                return True
            apply_proposal(plan, proposal)
            return True

        try:
            landed = self._registry.mutate(started.plan_id, apply)
        except PlanQueueError as exc:
            if exc.kind != ErrorKind.NOT_FOUND:
                raise
            return  # discarded meanwhile
        if landed and self._observer is not None:
            plan = self._registry.get(started.plan_id)
            event = (
                f'proposed {len(plan.steps)} steps' if proposal is not None
                else f'the split proposal failed: {plan.reason}'
            )
            self._observer(plan, event)


def files_of(plan: Plan) -> dict[str, str] | None:
    """The operator's split files (path → text), or None for a plan to split."""
    return {split_file.path: split_file.text for split_file in plan.split_files} or None


def apply_proposal(plan: Plan, proposal: SplitProposal) -> None:
    """The proposed split becomes the plan's steps, ready for review."""
    sources = proposal.files or ('',) * len(proposal.steps)
    plan.steps = [_step(draft, source) for draft, source in zip(proposal.steps, sources)]
    plan.notes = proposal.notes
    plan.status = PlanStatus.PROPOSED
    plan.reason = ''


def _drafts(steps: list[Step]) -> tuple[StepDraft, ...]:
    return tuple(
        StepDraft(
            id=step.id, title=step.title, brief=step.brief,
            depends_on=tuple(step.depends_on), repositories=tuple(step.repositories),
        )
        for step in steps
    )


def _step(draft: StepDraft, source_file: str = '') -> Step:
    return Step(
        id=draft.id, title=draft.title, brief=draft.brief,
        depends_on=list(draft.depends_on), repositories=list(draft.repositories),
        source_file=source_file,
    )
