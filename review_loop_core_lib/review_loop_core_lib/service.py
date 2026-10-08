"""Every task's review loops: start, stop, resume, look at, forget.

At most one loop runs per task. The service keeps the running ones and each
task's most recent finished one in memory, so the cheap ``summaries()`` an
indicator polls never touches the disk after the first call.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from review_loop_core_lib.review_loop_core_lib.data.state import (
    ReviewLoopPhase,
    ReviewLoopState,
    ReviewLoopStatus,
)
from review_loop_core_lib.review_loop_core_lib.ports import (
    ChatChannel,
    LoopObserver,
    LoopWording,
    Reviewer,
    TaskDiffSource,
)
from review_loop_core_lib.review_loop_core_lib.runner import (
    DEFAULT_MAX_ROUNDS,
    MAX_ROUNDS_LIMIT,
    ReviewLoopRunner,
    RunnerOptions,
)
from review_loop_core_lib.review_loop_core_lib.store import (
    ArtifactKind,
    ReviewLoopStore,
)


class ReviewLoopError(RuntimeError):
    """A loop could not be started; the message says why, for the operator."""


class ReviewLoopService(object):
    """The per-task registry of review loops."""

    def __init__(
        self,
        *,
        store: ReviewLoopStore,
        chat: ChatChannel,
        reviewer: Reviewer,
        wording: LoopWording,
        observer: LoopObserver | None = None,
        can_start: Callable[[str], str] | None = None,
        max_rounds: int = DEFAULT_MAX_ROUNDS,
        options: RunnerOptions | None = None,
        clock: Callable[[], float] = time.time,
        logger: logging.Logger | None = None,
    ) -> None:
        self._store = store
        self._chat = chat
        self._reviewer = reviewer
        self._wording = wording
        self._observer = observer
        # ``task_id -> refusal reason``, '' when the host is fine with a loop.
        self._can_start = can_start
        self._max_rounds = max(1, min(MAX_ROUNDS_LIMIT, int(max_rounds)))
        self._options = options
        self._clock = clock
        self._logger = logger or logging.getLogger('ReviewLoopService')
        self._lock = threading.Lock()
        self._runners: dict[str, ReviewLoopRunner] = {}
        self._latest: dict[str, ReviewLoopState] | None = None
        # Tasks mid-forget: a loop finishing meanwhile must not re-cache them.
        self._forgetting: set[str] = set()

    # ----- starting and stopping -----

    def start(
        self,
        task_id: str,
        *,
        diff_source: TaskDiffSource,
        task_summary: str = '',
        task_description: str = '',
        max_rounds: int | None = None,
        self_check: bool = False,
        verify_tests: bool = False,
        confirm_clean: bool = False,
        extra_sweep: bool = False,
        model: str = '',
    ) -> ReviewLoopState:
        """Start a loop for ``task_id``; raises ``ReviewLoopError`` with a reason.

        ``max_rounds`` is the operator's choice for THIS loop (the service's
        default when None), held to ``1..MAX_ROUNDS_LIMIT``. The three stage
        options are off unless the host turns them on: ``self_check`` (the
        main chat reviews + fixes its own change first), ``verify_tests``
        ("clean" also needs the tests to pass) and ``confirm_clean`` (a clean
        verdict from a reviewer that saw the decision ledger needs a clean-room
        review to agree). ``extra_sweep`` goes further: ANY clean verdict needs
        one more clean-room review to agree — two clean reviews in a row.
        """
        self._refuse_to_start(task_id)
        with self._lock:
            self._refuse_if_running(task_id)
            self._store.open(task_id)
            state = ReviewLoopState.new(
                task_id, max_rounds=self._rounds_for(max_rounds), now=self._clock(),
                self_check=bool(self_check), verify_tests=bool(verify_tests),
                confirm_clean=bool(confirm_clean), extra_sweep=bool(extra_sweep),
                model=model,
            )
            runner = self._new_runner(
                state, diff_source=diff_source,
                task_summary=task_summary, task_description=task_description,
            )
        runner.start()
        return runner.snapshot()

    def resume(
        self,
        task_id: str,
        *,
        diff_source: TaskDiffSource,
        task_summary: str = '',
        task_description: str = '',
    ) -> ReviewLoopState:
        """Pick the task's latest loop up where it was cut off.

        Stopped, failed, interrupted or stuck: the loop finishes the step it
        was in (``ReviewLoopState.resume_point``) and goes on with its own
        round limit, stages, model, decisions and repeat counts — the same
        loop, not a new one. Raises ``ReviewLoopError`` with a reason.
        """
        self._refuse_to_start(task_id)
        latest = self._latest_states().get(task_id)
        with self._lock:
            self._refuse_if_running(task_id)
            if latest is None:
                raise ReviewLoopError('this task has no review loop to resume')
            point = latest.resume_point()
            if point is None:
                raise ReviewLoopError(
                    f'the last review loop ended {latest.status.value} — there is nothing '
                    'to resume; run a new loop instead',
                )
            self._store.open(task_id)
            state = latest.copy()
            state.reopen(point, self._clock())
            runner = self._new_runner(
                state, diff_source=diff_source,
                task_summary=task_summary, task_description=task_description,
            )
        runner.start(resume=point)
        return runner.snapshot()

    def _refuse_to_start(self, task_id: str) -> None:
        if not self._store.is_valid_task_id(task_id):
            raise ReviewLoopError(f'not a task id that can be reviewed: {task_id!r}')
        refusal = self._can_start(task_id) if self._can_start is not None else ''
        if refusal:
            raise ReviewLoopError(refusal)

    def _refuse_if_running(self, task_id: str) -> None:
        """Called with ``_lock`` held."""
        running = self._runners.get(task_id)
        if running is not None and running.is_alive:
            raise ReviewLoopError('a review loop is already running for this task')

    def _new_runner(
        self, state: ReviewLoopState, *, diff_source: TaskDiffSource,
        task_summary: str, task_description: str,
    ) -> ReviewLoopRunner:
        """A runner for ``state``, registered as the task's. ``_lock`` held."""
        runner = ReviewLoopRunner(
            state,
            diff_source=diff_source,
            reviewer=self._reviewer,
            chat=self._chat,
            store=self._store,
            wording=self._wording,
            task_summary=task_summary,
            task_description=task_description,
            observer=self._observer,
            on_finished=self._runner_finished,
            options=self._options,
            clock=self._clock,
            logger=self._logger,
        )
        self._runners[state.task_id] = runner
        return runner

    def _rounds_for(self, requested: int | None) -> int:
        if requested is None:
            return self._max_rounds
        return max(1, min(MAX_ROUNDS_LIMIT, int(requested)))

    def stop(self, task_id: str, reason: str = 'stopped by the operator') -> bool:
        """Stop the task's running loop; False when none was running."""
        with self._lock:
            runner = self._runners.get(task_id)
        if runner is None or not runner.is_alive:
            return False
        runner.cancel(reason)
        return True

    def is_running(self, task_id: str) -> bool:
        with self._lock:
            runner = self._runners.get(task_id)
        return runner is not None and runner.is_alive

    def _runner_finished(self, runner: ReviewLoopRunner) -> None:
        final = runner.snapshot()
        # Load the cache first: recording only into an already-loaded cache
        # left a finished loop on disk alone — and gone, if that write failed.
        self._latest_states()
        with self._lock:
            if self._runners.get(runner.task_id) is runner:
                del self._runners[runner.task_id]
            if final.task_id not in self._forgetting:
                self._latest[final.task_id] = final

    # ----- looking -----

    def state(self, task_id: str) -> ReviewLoopState | None:
        """The running loop, else the task's most recent finished one."""
        with self._lock:
            runner = self._runners.get(task_id)
        if runner is not None:
            return runner.snapshot()
        latest = self._latest_states()
        state = latest.get(task_id)
        return state.copy() if state is not None else None

    def summaries(self) -> dict[str, dict]:
        """``task_id -> summary`` for every task that has had a loop."""
        result = {task_id: state.summary() for task_id, state in self._latest_states().items()}
        with self._lock:
            runners = list(self._runners.values())
        for runner in runners:
            result[runner.task_id] = runner.snapshot().summary()
        return result

    def artifact(
        self, task_id: str, loop_id: str, round_number: int, kind: ArtifactKind | str,
    ) -> str | None:
        """One round's saved text, or None. Bad ids or kinds are just None."""
        try:
            return self._store.read_artifact(
                task_id, loop_id, int(round_number), ArtifactKind(kind),
            )
        except ValueError:
            return None

    def _latest_states(self) -> dict[str, ReviewLoopState]:
        with self._lock:
            if self._latest is None:
                self._latest = self._store.latest_by_task()
            return dict(self._latest)

    # ----- lifecycle -----

    def forget(self, task_id: str, *, join_seconds: float = 2.0) -> None:
        """Stop and delete every trace of the task's loops (the task was removed)."""
        self._latest_states()
        with self._lock:
            self._forgetting.add(task_id)
            runner = self._runners.pop(task_id, None)
            self._store.close(task_id)
        if runner is not None:
            runner.cancel('the task was removed')
            runner.join(join_seconds)
        self._store.delete_task(task_id)
        with self._lock:
            self._latest.pop(task_id, None)
            self._forgetting.discard(task_id)

    def mark_interrupted(self, reason: str) -> int:
        """Close loops a previous run of the host left RUNNING; returns how many.

        A loop's thread does not survive its process, so on the next start the
        saved state still says running. It is never resumed on its own — the
        chat it was waiting on is gone, and the operator decides between
        ``resume`` and a new loop — just recorded as interrupted.
        """
        now = self._clock()
        closed = 0
        for task_id, state in self._latest_states().items():
            if state.status is not ReviewLoopStatus.RUNNING or self.is_running(task_id):
                continue
            state.status = ReviewLoopStatus.INTERRUPTED
            state.phase = ReviewLoopPhase.DONE
            state.phase_started_at = now
            state.finished_at = now
            state.reason = reason
            state.waiting_for = ''
            current = state.current_round
            if current is not None and not current.outcome:
                current.outcome = 'stopped'
            self._store.save(state)
            with self._lock:
                self._latest[task_id] = state
            closed += 1
        return closed

    def shutdown(self, reason: str, *, join_seconds: float = 2.0) -> None:
        """Stop every running loop as INTERRUPTED (the host is going down)."""
        with self._lock:
            runners = list(self._runners.values())
        for runner in runners:
            runner.cancel(reason, status=ReviewLoopStatus.INTERRUPTED)
        for runner in runners:
            runner.join(join_seconds)
