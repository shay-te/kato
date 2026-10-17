"""The background pass that moves every plan forward.

Each tick, a split waiting for review is first checked against the chat it
was proposed in: the operator talks to the agent there, and a reply carrying a
new split replaces the proposal. Then, for every plan with steps in flight:

1. **Sync** — a plan on a shared tracker reads its record back: another host
   may have taken it over, stopped it, or re-created a step's task.
2. **Track** — ask the host where each step's task stands (every step that has
   a task: one taken before its turn shows up at once).
3. **Merge** — for steps whose task is done, look for the merge (with backoff;
   the destination's history when no change request was made).
4. **Unblock** — steps whose dependencies are all satisfied become ready.
5. **Release** — a running plan hands its ready steps to the host; on a shared
   tracker only its driver does.
6. **Finish** — a plan whose steps are all satisfied or cancelled is finished.
7. **Publish** — the driver writes what changed where every host can read it.

The tick holds no state of its own: everything it needs is in the saved plans,
so a restart simply carries on. Calls to the host happen outside the registry's
lock; their results are applied only if the step is still where it was.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from plan_queue_core_lib.plan_queue_core_lib.data.plan import (
    FailReason,
    Plan,
    PlanMode,
    PlanStatus,
    RepoMerge,
    RepoMergeState,
    SatisfiedBy,
    Step,
    StepStatus,
)
from plan_queue_core_lib.plan_queue_core_lib.launching import build_request, release_task
from plan_queue_core_lib.plan_queue_core_lib.merge import (
    MergeVerdict,
    classify_repository,
    found_nothing,
    merge_verdict,
    next_probe_delay,
    step_since,
)
from plan_queue_core_lib.plan_queue_core_lib.plan_record import acceptable, parse_record, render_record
from plan_queue_core_lib.plan_queue_core_lib.proposer_prompt import SPLIT_OPEN
from plan_queue_core_lib.plan_queue_core_lib.proposing import apply_proposal, files_of
from plan_queue_core_lib.plan_queue_core_lib.split import SplitProposalError, parse_task_split
from plan_queue_core_lib.plan_queue_core_lib.ports import (
    HistoryProbe,
    Ownership,
    PlanObserver,
    PlanPublisher,
    PlanRecord,
    PlanRecordSource,
    PullRequestProbe,
    SplitConversation,
    TaskLauncher,
    TaskObservation,
    TaskProgress,
)
from plan_queue_core_lib.plan_queue_core_lib.progress import change_from_observation
from plan_queue_core_lib.plan_queue_core_lib.registry import PlanRegistry
from plan_queue_core_lib.plan_queue_core_lib.release import (
    blocked_reason,
    is_finished,
    releasable,
    unblocked,
)

MAX_PROBES_PER_TICK = 4
NO_PR_REASON = 'no pull request found — no changes? Count it as done'
MERGE_WAIT_REASON = 'waiting for the merge'
PUBLISH_RETRY_SECONDS = 60
_FOLLOWED_STATUS = {
    'approved': PlanStatus.APPROVED,
    'running': PlanStatus.RUNNING,
    'paused': PlanStatus.PAUSED,
    'finished': PlanStatus.FINISHED,
    'cancelled': PlanStatus.CANCELLED,
}


class PlanTicker(object):
    def __init__(
        self,
        registry: PlanRegistry,
        *,
        progress: TaskProgress,
        probe: PullRequestProbe,
        launcher: Callable[[Plan], TaskLauncher | None],
        clock: Callable[[], float],
        observer: PlanObserver | None = None,
        logger: logging.Logger | None = None,
        ownership: Ownership | None = None,
        publisher: PlanPublisher | None = None,
        record_source: PlanRecordSource | None = None,
        history: HistoryProbe | None = None,
        conversation: SplitConversation | None = None,
    ) -> None:
        self._conversation = conversation
        self._registry = registry
        self._progress = progress
        self._probe = probe
        self._launcher = launcher
        self._clock = clock
        self._observer = observer
        self._logger = logger or logging.getLogger('PlanTicker')
        self._ownership = ownership
        self._publisher = publisher
        self._record_source = record_source
        self._history = history

    def tick(self) -> None:
        probes_left = MAX_PROBES_PER_TICK
        for plan in self._registry.all():
            if plan.status in (PlanStatus.PROPOSED, PlanStatus.PROPOSE_FAILED):
                self._converse(plan)
                continue
            if plan.mode != PlanMode.LOCAL:
                self._sync(plan)
            if self._registry.get(plan.plan_id).status.has_steps_in_flight:
                self._track(self._registry.get(plan.plan_id))
                probes_left = self._check_merges(plan.plan_id, probes_left)
                self._unblock(plan.plan_id)
                self._release(plan.plan_id)
                self._finish(plan.plan_id)
            self._publish(plan.plan_id)

    def identity(self) -> str:
        """This host's identity, or '' when it can't be told."""
        if self._ownership is None:
            return ''
        try:
            return str(self._ownership.identity() or '')
        except Exception as exc:
            self._logger.warning('could not tell which host this is: %s', exc)
            return ''

    # ----- 0. the split's conversation -----

    def _converse(self, plan: Plan) -> None:
        """A newer reply in the chat the split was proposed in: when it holds
        a split, it becomes the proposal (the operator steered it there)."""
        if self._conversation is None:
            return
        try:
            reply = self._conversation.latest_reply(plan.source_task_id, plan.chat_seen_at)
        except Exception as exc:
            self._logger.warning('could not read the chat of %s: %s', plan.source_task_id, exc)
            return
        if reply is None:
            return
        received_at, text = reply
        proposal, problem = None, ''
        if SPLIT_OPEN in str(text or ''):
            try:
                proposal = parse_task_split(text, allowed_repositories=plan.repository_ids, files=files_of(plan))
            except SplitProposalError as exc:
                problem = str(exc)

        def apply(live: Plan) -> str:
            if live.status not in (PlanStatus.PROPOSED, PlanStatus.PROPOSE_FAILED) or live.chat_seen_at >= received_at:
                return ''
            live.chat_seen_at = received_at
            if proposal is not None:
                apply_proposal(live, proposal)
                live.revision += 1
                return 'the split was changed in the chat'
            if problem:
                live.reason = f'the newest split in the chat could not be used: {problem}'
            return ''

        event = self._registry.mutate(plan.plan_id, apply)
        if event:
            self._note(plan.plan_id, event)

    # ----- 1. sync -----

    def _sync(self, plan: Plan) -> None:
        if self._record_source is None:
            return
        if plan.mode == PlanMode.TRACKER and plan.record_version > plan.published_version:
            return  # this host's own change is newer than the tracker: publish it first
        try:
            record = self._record_source.latest(plan.source_task_id)
        except Exception as exc:
            self._logger.warning('could not read the plan record of %s: %s', plan.source_task_id, exc)
            return
        if record is None or record.plan_id != plan.plan_id:
            return
        problem = acceptable(record, previous=as_record(plan))
        if problem:
            self._logger.warning('not following the plan record on %s: %s', plan.source_task_id, problem)
            return
        me = self.identity()

        def apply(live: Plan) -> str:
            return apply_record(live, record, me)

        event = self._registry.mutate(plan.plan_id, apply)
        if event:
            self._note(plan.plan_id, event)

    # ----- 2. track -----

    def _track(self, plan: Plan) -> None:
        watched = [
            step for step in plan.steps
            if step.task_id and not step.status.is_terminal and step.status != StepStatus.FAILED
        ]
        if not watched:
            return
        try:
            observations = self._progress.observe([step.task_id for step in watched])
        except Exception:
            self._logger.exception('could not read the tasks of plan %s', plan.plan_id)
            return
        now = self._clock()
        for step in watched:
            observation = observations.get(step.task_id)
            if observation is not None:
                self._observed(plan.plan_id, step, observation, now)

    def _observed(self, plan_id: str, step: Step, observation: TaskObservation, now: float) -> None:
        change = change_from_observation(step, observation, now=now)

        def apply(live: Plan) -> bool:
            target = live.step(step.id)
            if target is None or (target.status, target.task_id) != (step.status, step.task_id):
                return False
            changed = _note_worker(target, observation)
            if change is None:
                return changed
            target.status = change.status
            target.reason_code = change.reason_code
            target.reason = change.reason
            if change.taken and not target.released_at:
                target.released_at = now
            if change.status == StepStatus.SATISFIED:
                target.satisfied_by = change.satisfied_by
                target.satisfied_at = now
                live.touch_record()
            if change.status == StepStatus.AWAITING_MERGE:
                target.merge.next_check_at = now
                target.merge.attempts = 0
                target.reason = MERGE_WAIT_REASON
            return True

        if self._registry.mutate(plan_id, apply) and change is not None:
            self._note(plan_id, f'{step.task_id}: {change.status.value}')

    # ----- 3. merge -----

    def _check_merges(self, plan_id: str, budget: int) -> int:
        now = self._clock()
        for step in self._registry.get(plan_id).steps:
            if budget <= 0:
                break
            if not _due(step, now):
                continue
            budget -= 1
            repos = [self._repo_state(step, repo) for repo in step.repositories]
            in_history = self._merged_in_history(step, repos)
            self._apply_merge(plan_id, step, repos, in_history, now)
        return budget

    def _repo_state(self, step: Step, repository_id: str) -> RepoMerge:
        try:
            facts = self._probe.pull_requests(step.task_id, repository_id)
        except Exception as exc:
            self._logger.warning(
                'could not look up the pull requests of %s in %s: %s',
                step.task_id, repository_id, exc,
            )
            return RepoMerge(repository_id, RepoMergeState.UNKNOWN)
        return classify_repository(
            repository_id, facts, since=step_since(step.created_at, step.released_at),
        )

    def _merged_in_history(self, step: Step, repos: list[RepoMerge]) -> bool:
        """A done step with no change request anywhere: was it merged straight
        into the destination (a commit naming its task)?"""
        if self._history is None or step.status != StepStatus.AWAITING_MERGE or not found_nothing(repos):
            return False
        since = step_since(step.created_at, step.released_at)
        for repo in repos:
            try:
                if self._history.merged_in_history(step.task_id, repo.repository_id, since=since):
                    return True
            except Exception as exc:
                self._logger.warning(
                    'could not look for %s in the history of %s: %s', step.task_id, repo.repository_id, exc,
                )
        return False

    def _apply_merge(
        self, plan_id: str, step: Step, repos: list[RepoMerge], in_history: bool, now: float,
    ) -> None:
        verdict = merge_verdict(repos)

        def apply(live: Plan) -> str:
            target = live.step(step.id)
            if target is None or (target.status, target.task_id) != (step.status, step.task_id):
                return ''
            target.merge.repos = repos
            target.merge.checked_at = now
            target.merge.forced = False
            if target.status != StepStatus.AWAITING_MERGE:
                return ''  # a "Check now" on a step not done yet: shown, never acted on
            outcome = _settle(target, verdict, in_history, repos, now)
            if outcome == 'merged':
                live.touch_record()
            return outcome

        outcome = self._registry.mutate(plan_id, apply)
        if outcome:
            self._note(plan_id, f'{step.task_id}: {outcome}')

    # ----- 4. unblock -----

    def _unblock(self, plan_id: str) -> None:
        def apply(live: Plan) -> list[str]:
            freed = []
            for target in unblocked(live):
                target.status = StepStatus.READY
                target.reason = ''
                freed.append(target.id)
            for target in live.steps:
                if target.status == StepStatus.BLOCKED:
                    target.reason = blocked_reason(live, target)
            return freed

        self._registry.mutate(plan_id, apply)

    # ----- 5. release -----

    def _release(self, plan_id: str) -> None:
        plan = self._registry.get(plan_id)
        if plan.status != PlanStatus.RUNNING or not self._may_release(plan):
            return
        launcher = self._launcher(plan)
        if launcher is None or not _available(launcher):
            return
        for step in releasable(plan):
            claimed_at = self._clock()
            if not self._claim(plan_id, step, claimed_at):
                continue
            request = build_request(self._registry.get(plan_id), step)
            outcome = release_task(launcher, request, step.task_id)
            self._record_release(plan_id, step, claimed_at, outcome)

    def _may_release(self, plan: Plan) -> bool:
        """Only the driver releases a shared plan — checked BEFORE a step is
        claimed, so a host that may not release leaves it ready, not failed."""
        if plan.mode == PlanMode.LOCAL:
            return True
        if plan.mode == PlanMode.FOLLOWED:
            return False
        me = self.identity()
        return bool(me) and me == plan.driver

    def _claim(self, plan_id: str, step: Step, claimed_at: float) -> bool:
        def apply(live: Plan) -> bool:
            target = live.step(step.id)
            if live.status != PlanStatus.RUNNING or target is None or target.status != StepStatus.READY:
                return False
            target.status = StepStatus.STARTING
            target.released_at = claimed_at
            target.reason = ''
            return True

        return self._registry.mutate(plan_id, apply)

    def _record_release(self, plan_id: str, step: Step, claimed_at: float, outcome) -> None:
        def apply(live: Plan) -> bool:
            target = live.step(step.id)
            if target is None or target.status != StepStatus.STARTING or target.released_at != claimed_at:
                return False
            if not outcome.ok:
                target.status = StepStatus.FAILED
                target.reason_code = outcome.fail_reason.value
                target.reason = outcome.message
                return True
            if target.task_id and target.task_id != outcome.task_id:
                target.previous_task_ids.append(target.task_id)
                live.touch_record()  # another host must learn the new task
            target.task_id = outcome.task_id
            target.reason_code = ''
            return True

        if self._registry.mutate(plan_id, apply):
            label = outcome.task_id or step.id
            self._note(plan_id, f'released {label}' if outcome.ok else f'could not start {label}: {outcome.message}')

    # ----- 6. finish -----

    def _finish(self, plan_id: str) -> None:
        def apply(live: Plan) -> bool:
            if live.status.has_steps_in_flight and is_finished(live):
                live.status = PlanStatus.FINISHED
                live.reason = ''
                live.touch_record()
                return True
            return False

        if self._registry.mutate(plan_id, apply):
            self._note(plan_id, 'finished — every step is done')

    # ----- 7. publish -----

    def _publish(self, plan_id: str) -> None:
        plan = self._registry.find(plan_id)
        if plan is None or self._publisher is None or plan.mode != PlanMode.TRACKER:
            return
        if plan.record_version <= plan.published_version or self._clock() < plan.record_retry_at:
            return
        if self.identity() != plan.driver:
            return
        version = plan.record_version
        try:
            self._publisher.publish(plan)
        except Exception as exc:
            self._logger.warning('could not publish plan %s: %s', plan_id, exc)
            retry_at = self._clock() + PUBLISH_RETRY_SECONDS

            def failed(live: Plan) -> bool:
                live.record_retry_at = retry_at
                return True

            self._registry.mutate(plan_id, failed)
            return

        def published(live: Plan) -> bool:
            live.published_version = max(live.published_version, version)
            live.record_retry_at = 0.0
            return True

        self._registry.mutate(plan_id, published)

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


def as_record(plan: Plan) -> PlanRecord | None:
    """The plan as it was last published (what a newer record is checked
    against). None before it has any task."""
    record = parse_record(render_record(plan), author=plan.driver_user)
    return record


def apply_record(live: Plan, record: PlanRecord, me: str) -> str:
    """Take in what the tracker says about a shared plan: who drives it, its
    tasks, and — on a follower — its status. Returns a note ('' = nothing new)."""
    events = []
    if record.driver != live.driver:
        live.driver = record.driver
        live.driver_user = record.driver_user
        live.mode = PlanMode.TRACKER if me and me == record.driver else PlanMode.FOLLOWED
        events.append(f'now driven by {record.driver_user}')
    # ``acceptable`` holds the steps to the plan's own (same ids, same order),
    # so every entry names a step this plan has.
    for entry in record.steps:
        step = live.step(entry.step_id)
        if step.task_id != entry.task_id:
            step.previous_task_ids.append(step.task_id)
            step.task_id = entry.task_id
            events.append(f'{entry.step_id} is now {entry.task_id}')
        if live.mode == PlanMode.FOLLOWED:
            step.repositories = list(entry.repositories)
            if entry.done and not step.is_satisfied:
                step.status = StepStatus.SATISFIED
                step.satisfied_by = SatisfiedBy.DRIVER.value
                step.reason = ''
                events.append(f'{entry.task_id}: done')
    status = _FOLLOWED_STATUS.get(record.status)
    if live.mode == PlanMode.FOLLOWED and status is not None and live.status != status:
        live.status = status
        events.append(f'status {status.value}')
    return '; '.join(events)


def _settle(target: Step, verdict: MergeVerdict, in_history: bool, repos: list[RepoMerge], now: float) -> str:
    """A done step's merge verdict applied: 'merged', 'declined' or '' (keep waiting)."""
    if verdict == MergeVerdict.MERGED or in_history:
        by_request = verdict == MergeVerdict.MERGED
        target.status = StepStatus.SATISFIED
        target.satisfied_by = (SatisfiedBy.MERGED if by_request else SatisfiedBy.HISTORY).value
        target.satisfied_at = now
        target.reason = '' if by_request else 'merged without a pull request'
        return 'merged'
    if verdict == MergeVerdict.DECLINED:
        target.status = StepStatus.FAILED
        target.reason_code = FailReason.PR_DECLINED.value
        declined = [repo.url or repo.repository_id for repo in repos if repo.state == RepoMergeState.DECLINED]
        target.reason = 'closed without merging: ' + ', '.join(declined)
        return 'declined'
    target.merge.attempts += 1
    target.merge.next_check_at = now + next_probe_delay(target.merge.attempts - 1)
    target.reason = NO_PR_REASON if found_nothing(repos) else MERGE_WAIT_REASON
    return ''


def _note_worker(step: Step, observation: TaskObservation) -> bool:
    """Who has the step's task, and when the task was created."""
    changed = False
    if (step.worker, step.worker_handle) != (observation.assignee_name, observation.assignee):
        step.worker = observation.assignee_name
        step.worker_handle = observation.assignee
        changed = True
    if observation.created_at and step.created_at != observation.created_at:
        step.created_at = observation.created_at
        changed = True
    return changed


def _due(step: Step, now: float) -> bool:
    if not step.task_id:
        return False
    if step.merge.forced and step.status.is_released:
        return True
    return step.status == StepStatus.AWAITING_MERGE and step.merge.next_check_at <= now


def _available(launcher: TaskLauncher) -> bool:
    try:
        return bool(launcher.available())
    except Exception:
        return False
