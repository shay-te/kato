"""Running the agent for one operator comment.

A comment is not just a record: it is a unit of work with a lifecycle —
queued, in progress, completed or requeued — driven by an agent session that
may be alive, mid-turn, stalled, or gone. That lifecycle is this service's
only job; the comment records themselves belong to TaskCommentService.

Two invariants this code exists to hold:

* **One run at a time per task.** ``trigger_comment_run`` takes a
  per-task lock across busy-check → IN_PROGRESS flip → send, because two
  concurrent triggers (a scan tick draining the queue and a fresh POST) could
  each pass the check and both ride the same RESULT — which surfaced as
  kato replying to a comment about a completely unrelated change.
* **A result belongs to the run that asked for it.** Each dispatch carries a
  marker and completion matches on it, so a late reply from a previous turn is
  never attached to the comment that happens to be in progress now.
"""

from __future__ import annotations

import threading
import time
import uuid

from agent_core_lib.agent_core_lib.helpers.comment_prompt import (
    CommentThreadSpec,
    build_comment_prompt_context,
)
from claude_core_lib.claude_core_lib.session.streaming import (
    TURN_ACK_GRACE_SECONDS as _COMMENT_SEND_ACK_GRACE_SECONDS,
)
from sandbox_core_lib.sandbox_core_lib.workspace_delimiter import (
    wrap_untrusted_workspace_content,
)

from kato_core_lib.helpers.comment_store_utils import comment_store_for
from kato_core_lib.helpers.late_binding import provider_for
from kato_core_lib.helpers.logging_utils import configure_logger
from kato_core_lib.helpers.workspace_repo_utils import sibling_repository_dirs

# Stamped on every dispatch so a result can be matched to the run that asked
# for it — see ``_comment_result_belongs_to_run``.
_COMMENT_RUN_MARKER_PREFIX = 'KATO_LOCAL_COMMENT_RUN:'


class TaskCommentRunService(object):
    """Queue → dispatch → completion for a task's operator comments."""

    def __init__(
        self,
        *,
        comment_service,
        session_manager=None,
        workspace_manager=None,
        parallel_task_runner=None,
        planning_session_runner=None,
        cleanup_service=None,
        logger=None,
    ) -> None:
        self._get_comment_service = provider_for(comment_service)
        self._get_session_manager = provider_for(session_manager)
        self._get_workspace_manager = provider_for(workspace_manager)
        self._get_parallel_task_runner = provider_for(parallel_task_runner)
        self._get_planning_session_runner = provider_for(planning_session_runner)
        self._get_cleanup_service = provider_for(cleanup_service)
        self._logger_getter = provider_for(
            logger if logger is not None else configure_logger('TaskCommentRunService'),
        )
        # Per-task lock serialising busy-check → IN_PROGRESS flip → send.
        self._comment_dispatch_locks: dict[str, threading.Lock] = {}
        self._comment_dispatch_locks_lock = threading.Lock()

    @property
    def logger(self):
        """The host's CURRENT logger — resolved per call, never captured."""
        return self._logger_getter()

    @property
    def _comment_service(self):
        """The comment records this service runs work for."""
        return self._get_comment_service()

    @property
    def _session_manager(self):
        return self._get_session_manager()

    @property
    def _workspace_manager(self):
        return self._get_workspace_manager()

    @property
    def _parallel_task_runner(self):
        return self._get_parallel_task_runner()

    @property
    def _planning_session_runner(self):
        return self._get_planning_session_runner()

    @property
    def _cleanup_service(self):
        return self._get_cleanup_service()

    def _comment_store_for(self, task_id: str):
        """This task's local comment store, or ``None`` when it has no workspace.

        Asked of the service that owns the records rather than resolved again
        here: one lookup, one place to stub, and no way for the two to
        disagree about which store a task has.
        """
        comments = self._comment_service
        if comments is None:
            return comment_store_for(self._workspace_manager, task_id)
        return comments.comment_store(task_id)

    def drain_next_queued_task_comment(self, task_id: str) -> dict[str, object]:
        """Start the oldest queued local diff comment for this task if possible."""
        store = self._comment_store_for(task_id)
        if store is None:
            return {'ok': False, 'started': False, 'error': 'no workspace for task'}
        record = store.next_queued()
        if record is None:
            return {'ok': True, 'started': False, 'comment_id': ''}
        started = self.trigger_comment_run(str(task_id), record.id)
        return {'ok': True, 'started': started, 'comment_id': record.id}

    def drain_all_queued_task_comments(self) -> list[dict[str, object]]:
        """Drain one queued local diff comment for every task workspace.

        Server-side, browser-independent. Previously a queued comment
        was ONLY drained when a ``RESULT`` event flowed through an
        open browser SSE (or a browser reconnected to a dead session)
        — so a comment queued while Claude was busy stayed ``QUEUED``
        forever if nobody happened to be watching that task's tab when
        the turn finished (the "3-hour-old queued comment" report).
        The scan loop now calls this every cycle so a queued comment
        is picked up on the next idle transition no matter what the
        UI is doing. ``drain_next_queued_task_comment`` is a cheap
        no-op when nothing is queued or the turn is still busy, so
        running it across every workspace each tick is safe.
        """
        results: list[dict[str, object]] = []
        for record in self._workspace_records():
            task_id = str(getattr(record, 'task_id', '') or '').strip()
            if not task_id:
                continue
            try:
                outcome = self.drain_next_queued_task_comment(task_id)
            except Exception:
                self.logger.exception(
                    'queued-comment drain failed for task %s', task_id,
                )
                continue
            if outcome.get('started'):
                results.append({'task_id': task_id, **outcome})
        return results

    def requeue_stuck_in_progress_comments(self) -> list[dict[str, object]]:
        """Reset comments orphaned in IN_PROGRESS by a kato restart.

        ``trigger_comment_run`` marks a comment ``IN_PROGRESS``
        once the agent accepts the prompt. If kato is killed / restarted
        mid-run the agent subprocess dies but the
        on-disk comment stays ``IN_PROGRESS`` forever — and
        ``next_queued()`` only ever returns ``QUEUED`` comments, so
        the scan-loop drain never re-dispatches it and (with lazy
        resume) the chat session never wakes. That's the "I restarted
        kato and the conversation with my comment is still sleeping"
        report.

        Mirrors the boot-time ``_reset_stuck_workspace_statuses``
        recovery: at boot no streaming session is alive yet, so any
        ``IN_PROGRESS`` comment is by definition stale — flip it back
        to ``QUEUED`` so the very next scan tick drains it and
        respawns the session. Safe to run only at boot for that
        reason; do NOT call it while sessions may be live.
        """
        from kato_core_lib.comment_core_lib import KatoCommentStatus

        requeued: list[dict[str, object]] = []
        for record in self._workspace_records():
            task_id = str(getattr(record, 'task_id', '') or '').strip()
            if not task_id:
                continue
            store = self._comment_store_for(task_id)
            if store is None:
                continue
            try:
                comments = store.list()
            except Exception:
                self.logger.exception(
                    'failed to list comments while requeueing task %s', task_id,
                )
                continue
            for comment in comments:
                if comment.kato_status != KatoCommentStatus.IN_PROGRESS.value:
                    continue
                try:
                    store.update_kato_status(
                        comment.id,
                        kato_status=KatoCommentStatus.QUEUED.value,
                    )
                except Exception:
                    self.logger.exception(
                        'failed to requeue stuck comment %s on task %s',
                        comment.id, task_id,
                    )
                    continue
                requeued.append(
                    {'task_id': task_id, 'comment_id': comment.id},
                )
        return requeued

    def requeue_orphaned_in_progress_comments(self) -> list[dict[str, object]]:
        """Runtime recovery for comments orphaned IN_PROGRESS.

        ``requeue_stuck_in_progress_comments`` only runs at BOOT (every
        session is dead then). At RUNTIME a comment can still be orphaned:
        the session it was dispatched into is terminated/replaced — a
        review-fix respawn, an effort-change respawn, a forget+re-adopt —
        WITHOUT a ``RESULT`` that ``advance_finished_comment_runs`` could
        match. Its ``IN_PROGRESS`` state then blocks the whole task's
        queue forever: ``next_queued`` never returns it, and
        ``_task_has_in_progress_comment`` makes every QUEUED comment
        decline to start. That's the "comment never executed" report —
        the queue stays stuck until the next kato restart re-queues it.

        Per task, requeue an IN_PROGRESS comment ONLY when its run is
        provably gone: the task has NO live subprocess AND is not
        mid-turn, AND the run started more than the ack grace ago. The
        grace stops a comment whose session is still SPAWNING (briefly
        not-alive) from being yanked back and double-dispatched. A
        legitimately running comment (live, busy session) is never
        touched. Runs AFTER ``advance_finished_comment_runs`` in the
        watcher tick, so a comment whose RESULT is still in a live buffer
        is completed first and only true orphans are requeued.
        """
        from kato_core_lib.comment_core_lib import KatoCommentStatus

        requeued: list[dict[str, object]] = []
        now = time.time()
        for record in self._workspace_records():
            task_id = str(getattr(record, 'task_id', '') or '').strip()
            if not task_id:
                continue
            # A live subprocess (or a busy turn) might still be working
            # the comment — leave it on the WORKING badge.
            session = None
            if self._session_manager is not None:
                try:
                    session = self._session_manager.get_session(task_id)
                except Exception:
                    session = None
            if session is not None and getattr(session, 'is_alive', False):
                continue
            if self._task_has_busy_turn(task_id):
                continue
            store = self._comment_store_for(task_id)
            if store is None:
                continue
            try:
                comments = store.list()
            except Exception:
                self.logger.exception(
                    'failed to list comments requeuing orphans for task %s',
                    task_id,
                )
                continue
            for comment in comments:
                if comment.kato_status != KatoCommentStatus.IN_PROGRESS.value:
                    continue
                started = float(
                    getattr(comment, 'kato_run_started_at_epoch', 0.0) or 0.0,
                )
                # Unknown start time → treat as an old orphan (requeue).
                if started > 0 and (now - started) < _COMMENT_SEND_ACK_GRACE_SECONDS:
                    continue
                try:
                    store.update_kato_status(
                        comment.id,
                        kato_status=KatoCommentStatus.QUEUED.value,
                    )
                except Exception:
                    self.logger.exception(
                        'failed to requeue orphaned comment %s on task %s',
                        comment.id, task_id,
                    )
                    continue
                self.logger.info(
                    'requeued orphaned IN_PROGRESS comment %s on task %s '
                    '(session gone, no result) — drain will rerun it',
                    comment.id, task_id,
                )
                requeued.append(
                    {'task_id': task_id, 'comment_id': comment.id},
                )
        return requeued

    def complete_in_progress_task_comments(
        self,
        task_id: str,
        *,
        success: bool,
        result_text: str = '',
        result_received_at_epoch: float = 0.0,
    ) -> list[dict[str, object]]:
        """Move a task's IN_PROGRESS comments out when its turn ends.

        ``trigger_comment_run`` marks a comment ``IN_PROGRESS``
        once the streaming session accepts it, but nothing moved it OUT
        when the turn finished — so a comment
        kato actually completed sat on the "kato working" badge
        forever (and a restart's ``requeue_stuck_in_progress_comments``
        would redo the already-done work). Called from the
        RESULT-event handler: the turn that just ended is the one the
        in-progress comment was dispatched into, so ``success`` moves a
        fix request to ``ADDRESSED`` and a question-only answer to
        ``WAITING``; an errored turn moves to ``FAILED``.

        Fix requests reuse :meth:`mark_comment_addressed` with
        ``post_remote_reply=False`` — the auto-flip must not spam the
        source platform on every turn; the operator's explicit
        "Mark addressed" / Resolve still drives any remote reply.
        """
        # Hard invariant: a comment must NEVER be marked addressed while
        # Claude is still working — it stays on the WORKING badge. A
        # result can reach this method that does NOT belong to the
        # in-progress comment's turn: a browser replaying the session
        # backlog on reconnect, a resumed session's history, or a stale
        # result still sitting in the buffer while THIS comment's own
        # turn is in flight (``user_messages_sent > result_events_received``).
        # Completing then attaches the WRONG turn's answer and flips the
        # badge to ADDRESSED while the real work is still running (the
        # "kato replied instantly with an unrelated answer and never did
        # the work" report). If the session is busy, leave every comment
        # IN_PROGRESS — the live RESULT for this comment's own turn, or
        # the scan-loop fallback once the turn truly ends, completes it
        # with the right answer.
        if self._task_has_busy_turn(task_id):
            return []

        store = self._comment_store_for(task_id)
        if store is None:
            return []
        # Serialize the whole read→reply→status-flip against the SAME per-task
        # lock the dispatch path uses. This method runs once PER SSE connection
        # (``_follow_live_session`` tails events per browser tab) AND from the
        # scan-loop fallback (``advance_finished_comment_runs``) — all on the
        # same live RESULT. Without the lock two callers both read the comment
        # as IN_PROGRESS and each post a reply (+ a double ``mark_comment_
        # addressed``), the "he answered my comment twice" report. Re-listing
        # INSIDE the lock means the second caller sees the first's flip and
        # skips. The drain stays OUTSIDE the lock — it acquires this same
        # non-reentrant lock itself (``trigger_comment_run``).
        with self._comment_dispatch_lock_for(task_id):
            completed = self._complete_in_progress_comments_locked(
                store, task_id, success,
                result_text=result_text,
                result_received_at_epoch=result_received_at_epoch,
            )
        # Chain straight to the next queued comment the instant this turn
        # finishes, instead of stranding it on the slow scan-loop fallback
        # — the operator's "the next comment takes ages, and the last one
        # never runs" report. The turn we just completed left the session
        # idle, so starting the next one is safe; it is a no-op when the
        # queue is empty. Runs after success OR failure so a failed
        # comment never blocks the rest of the queue.
        if completed:
            try:
                self.drain_next_queued_task_comment(task_id)
            except Exception:
                self.logger.exception(
                    'failed to chain to next queued comment for task %s',
                    task_id,
                )
        return completed

    def _complete_in_progress_comments_locked(
        self,
        store,
        task_id: str,
        success: bool,
        *,
        result_text: str = '',
        result_received_at_epoch: float = 0.0,
    ) -> list[dict[str, object]]:
        """The critical section of ``complete_in_progress_task_comments``,
        run under the per-task dispatch lock: list IN_PROGRESS comments and
        complete the one(s) this turn's result belongs to. Lists INSIDE the
        lock so a second concurrent RESULT consumer sees the first's status
        flip and can't post a duplicate reply."""
        from kato_core_lib.comment_core_lib import KatoCommentStatus

        try:
            comments = store.list()
        except Exception:
            self.logger.exception(
                'failed to list comments completing task %s', task_id,
            )
            return []
        completed: list[dict[str, object]] = []
        for comment in comments:
            if comment.kato_status != KatoCommentStatus.IN_PROGRESS.value:
                continue
            if not self._comment_result_belongs_to_run(
                task_id, comment,
                result_text=result_text,
                require_marker=success,
                result_received_at_epoch=result_received_at_epoch,
            ):
                continue
            try:
                if success:
                    reply_text = self._strip_comment_run_marker(
                        result_text,
                        getattr(comment, 'kato_run_marker', ''),
                    )
                    self._add_comment_agent_reply(store, comment, reply_text)
                    if self._comment_expects_answer_only(task_id, comment):
                        store.update_kato_status(
                            comment.id,
                            kato_status=KatoCommentStatus.WAITING.value,
                        )
                        new_status = KatoCommentStatus.WAITING.value
                        self.logger.info(
                            'comment %s on task %s answered and left open '
                            '(question-only turn finished)', comment.id, task_id,
                        )
                    else:
                        self._comment_service.mark_comment_addressed(
                            task_id, comment.id, post_remote_reply=False,
                        )
                        new_status = KatoCommentStatus.ADDRESSED.value
                        self.logger.info(
                            'comment %s on task %s marked addressed '
                            '(agent turn finished)', comment.id, task_id,
                        )
                else:
                    store.update_kato_status(
                        comment.id,
                        kato_status=KatoCommentStatus.FAILED.value,
                        failure_reason='agent turn ended with an error',
                    )
                    new_status = KatoCommentStatus.FAILED.value
                    self.logger.warning(
                        'comment %s on task %s marked failed '
                        '(agent turn errored)', comment.id, task_id,
                    )
            except Exception:
                self.logger.exception(
                    'failed to complete comment %s on task %s',
                    comment.id, task_id,
                )
                continue
            completed.append({
                'task_id': task_id,
                'comment_id': comment.id,
                'kato_status': new_status,
            })
        return completed

    def advance_finished_comment_runs(self) -> list[dict[str, object]]:
        """Scan-loop fallback: advance IN_PROGRESS comments whose session has ended.

        Normal path: SSE RESULT event → ``_advance_task_comments_after_result``
        → ``complete_in_progress_task_comments``. Fallback (no SSE subscriber
        at the moment the turn finished): called each scan tick so the badge
        doesn't stay "⟳ kato working" after Claude has already finished.

        Safe to call at any time — skips tasks whose session is still alive
        and working so running comments are never interrupted.
        """
        from kato_core_lib.comment_core_lib import KatoCommentStatus

        advanced: list[dict[str, object]] = []
        for record in self._workspace_records():
            task_id = str(getattr(record, 'task_id', '') or '').strip()
            if not task_id:
                continue
            store = self._comment_store_for(task_id)
            if store is None:
                continue
            try:
                comments = store.list()
            except Exception:
                continue
            in_progress = [
                c for c in comments
                if c.kato_status == KatoCommentStatus.IN_PROGRESS.value
            ]
            if not in_progress:
                continue
            # A stalled session is alive but no longer consuming stdin
            # (the classic post-restart ``--resume`` respawn that never
            # picked up the piped message). ``_task_has_busy_turn``
            # reports it busy (``sent > received``), which would
            # otherwise pin the comment IN_PROGRESS forever — the scan
            # loop's safety net never fires, and the operator sees kato
            # ignore the comment after a restart. Requeue so the next
            # drain force-respawns a fresh session for it.
            if self._task_session_is_stalled(task_id):
                advanced.extend(
                    self._requeue_in_progress_comments(
                        store, task_id, in_progress, reason='session stalled',
                    )
                )
                continue
            # Leave comments alone while the session is mid-turn.
            if self._task_has_busy_turn(task_id):
                continue
            session = None
            if self._session_manager is not None:
                try:
                    session = self._session_manager.get_session(task_id)
                except Exception:
                    pass
            if session is not None and getattr(session, 'is_alive', False):
                # Session alive and idle: check if a RESULT turn already fired.
                # If so, advance now (SSE subscriber may have missed the event).
                last_result = None
                try:
                    for e in reversed(session.recent_events()):
                        if getattr(e, 'event_type', None) == 'result':
                            last_result = e
                            break
                except Exception:
                    pass
                if last_result is None:
                    # Session just spawned — no completed turn yet; wait.
                    continue
                is_error = bool((getattr(last_result, 'raw', None) or {}).get('is_error', False))
                result_text = str((getattr(last_result, 'raw', None) or {}).get('result') or '')
                results = self.complete_in_progress_task_comments(
                    task_id,
                    success=not is_error,
                    result_text=result_text,
                    result_received_at_epoch=float(
                        getattr(last_result, 'received_at_epoch', 0.0) or 0.0,
                    ),
                )
                advanced.extend(results)
                continue
            terminal = getattr(session, 'terminal_event', None) if session else None
            if terminal is not None:
                raw = getattr(terminal, 'raw', {}) or {}
                is_error = bool(raw.get('is_error', False))
                results = self.complete_in_progress_task_comments(
                    task_id, success=not is_error,
                    result_text=str(raw.get('result') or ''),
                    result_received_at_epoch=float(
                        getattr(terminal, 'received_at_epoch', 0.0) or 0.0,
                    ),
                )
                advanced.extend(results)
            else:
                # Session gone with no terminal event (crash / restart) — requeue.
                advanced.extend(
                    self._requeue_in_progress_comments(
                        store, task_id, in_progress,
                        reason='session gone without terminal event',
                    )
                )
        return advanced

    def _requeue_in_progress_comments(
        self, store, task_id: str, comments, *, reason: str,
    ) -> list[dict[str, object]]:
        """Flip IN_PROGRESS comments back to QUEUED so the next drain redispatches them.

        Shared by ``advance_finished_comment_runs``'s stalled-session
        and session-gone branches. Best-effort per comment: a failed
        ``update_kato_status`` is logged and skipped so one bad comment
        doesn't strand the rest.
        """
        from kato_core_lib.comment_core_lib import KatoCommentStatus

        requeued: list[dict[str, object]] = []
        for comment in comments:
            try:
                store.update_kato_status(
                    comment.id,
                    kato_status=KatoCommentStatus.QUEUED.value,
                )
                self.logger.info(
                    'comment %s on task %s requeued (%s)',
                    comment.id, task_id, reason,
                )
                requeued.append({
                    'task_id': task_id,
                    'comment_id': comment.id,
                    'action': 'requeued',
                })
            except Exception:
                self.logger.exception(
                    'failed to requeue stuck comment %s on task %s',
                    comment.id, task_id,
                )
        return requeued

    def trigger_comment_run(
        self, task_id: str, comment_id: str,
    ) -> bool:
        """Kick off a review-fix agent if the task has no live turn.

        Returns True when an agent run was started immediately,
        False when the comment was left in QUEUED for later
        draining. Wraps the actual launch in try/except so a
        bad spawn just leaves the comment queued — the operator
        can retry by reopening the comment or running the queue
        drain manually.

        Serialized per-task via ``_comment_dispatch_lock_for`` so
        the busy-check → IN_PROGRESS flip → ``send_user_message``
        sequence is atomic. Two concurrent triggers (scan-tick drain
        + browser POST) used to each pass the busy check before
        either had incremented ``user_messages_sent``, each dispatch
        its own comment, and then BOTH comments got the same
        result_text attached when the FIRST RESULT fired.
        """
        from kato_core_lib.comment_core_lib import KatoCommentStatus

        store = self._comment_store_for(task_id)
        if store is None:
            return False
        record = store.get(comment_id)
        if record is None:
            return False
        # A review-fix batch (or the task's implementation run) may be actively
        # operating this task's workspace clone via the parallel runner. That
        # path uses a DIFFERENT lock than ``_comment_dispatch_lock_for`` here,
        # so spawning a local comment agent now would run concurrent git ops on
        # the SAME on-disk checkout. Stay QUEUED; the scan-loop drain
        # redispatches once the runner frees the task.
        runner = self._parallel_task_runner
        if runner is not None:
            try:
                in_flight = runner.is_in_flight(task_id)
            except Exception:
                in_flight = False
            if in_flight:
                return False
        with self._comment_dispatch_lock_for(task_id):
            # Strict one-at-a-time: never dispatch a comment while another
            # is already IN_PROGRESS for this task. The session busy-checks
            # below can under-report — a respawned/resumed turn does not
            # always bump ``user_messages_sent`` — which previously let a
            # SECOND comment dispatch into the SAME turn. ``complete_in_
            # progress_task_comments`` then stamped that ONE turn's result
            # onto BOTH comments, so a reply landed on the wrong comment
            # (the "I added two comments, he replied to the wrong one"
            # report). The comment store is the authoritative serializer:
            # comments run strictly one-by-one, steered into the agent like
            # pending prompts. Fix A keeps the in-flight comment on WORKING
            # until its turn ends; the stall-requeue keeps it from getting
            # stuck; the post-turn drain releases the next one.
            if self._task_has_in_progress_comment(store, exclude_id=comment_id):
                return False
            stalled = self._task_session_is_stalled(task_id)
            live_turn_busy = self._task_has_busy_turn(task_id) and not stalled
            if live_turn_busy:
                # Stay queued; the queue drain (called from the
                # ``RESULT`` event handler) picks it up on the next
                # idle transition.
                return False
            run_marker = self._comment_run_marker()
            try:
                # A stalled session is alive but not consuming stdin, so
                # ``send_user_message`` would vanish into the void and the
                # comment would sit IN_PROGRESS forever. Force a fresh
                # respawn instead so the comment actually runs.
                started = self._run_comment_agent(
                    task_id, record, force_respawn=stalled,
                    run_marker=run_marker,
                )
            except Exception as exc:
                self.logger.exception(
                    'comment agent run failed for task %s comment %s',
                    task_id, comment_id,
                )
                store.update_kato_status(
                    comment_id,
                    kato_status=KatoCommentStatus.FAILED.value,
                    failure_reason=str(exc),
                )
                return False
            if not started:
                self.logger.warning(
                    'comment %s on task %s could not be started; left QUEUED '
                    'for the next scan tick to retry',
                    comment_id, task_id,
                )
                store.update_kato_status(
                    comment_id, kato_status=KatoCommentStatus.QUEUED.value,
                )
                return False
            start_run = getattr(store, 'start_kato_run', None)
            if callable(start_run):
                start_run(
                    comment_id,
                    started_at_epoch=time.time(),
                    result_count_before=self._task_result_count(task_id),
                    run_marker=run_marker,
                )
            else:
                store.update_kato_status(
                    comment_id, kato_status=KatoCommentStatus.IN_PROGRESS.value,
                )
        self.logger.info(
            'comment %s on task %s dispatched to the agent', comment_id, task_id,
        )
        return True

    @staticmethod
    def _task_has_in_progress_comment(store, exclude_id: str = '') -> bool:
        """True when the task already has a comment being worked on.

        The store is the authoritative serializer for comment dispatch:
        only one comment may be IN_PROGRESS at a time so a single agent
        turn's result can never be attributed to more than one comment.
        ``exclude_id`` skips the comment currently being considered so it
        doesn't block itself. Best-effort: a store read failure reports
        "not in progress" so a transient error can't wedge the queue.
        """
        from kato_core_lib.comment_core_lib import KatoCommentStatus

        try:
            comments = store.list()
        except Exception:
            return False
        target = str(exclude_id or '')
        for comment in comments:
            if str(getattr(comment, 'id', '') or '') == target:
                continue
            if getattr(comment, 'kato_status', '') == KatoCommentStatus.IN_PROGRESS.value:
                return True
        return False

    def _task_result_count(self, task_id: str) -> int:
        """Number of result events currently known for a task session."""
        if self._session_manager is None:
            return 0
        try:
            session = self._session_manager.get_session(task_id)
        except Exception:
            return 0
        if session is None:
            return 0
        try:
            return int(getattr(session, 'result_events_received', 0) or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _comment_run_marker() -> str:
        return f'{_COMMENT_RUN_MARKER_PREFIX}{uuid.uuid4().hex}'

    @staticmethod
    def _strip_comment_run_marker(result_text: str, marker: str) -> str:
        text = str(result_text or '')
        normalized_marker = str(marker or '')
        if not normalized_marker:
            return text
        return text.replace(normalized_marker, '').strip()

    def _comment_result_belongs_to_run(
        self,
        task_id: str,
        comment,
        *,
        result_text: str = '',
        require_marker: bool = True,
        result_received_at_epoch: float = 0.0,
    ) -> bool:
        """Reject stale results that predate the comment run dispatch."""
        run_marker = str(getattr(comment, 'kato_run_marker', '') or '')
        if run_marker and require_marker and run_marker in str(result_text or ''):
            # Marker echoed back: this result DEFINITIVELY belongs to the run.
            return True
        # Marker set but NOT echoed on a successful turn: LLMs don't reliably
        # append the sentinel (output truncation, a tool-ended turn, plain
        # non-compliance). Returning False here left the comment IN_PROGRESS
        # forever — and ``_task_has_in_progress_comment`` then blocked the
        # WHOLE task's queue until a kato restart. Fall through to the same
        # time / result-count staleness guard pre-marker comments use: a fresh
        # turn's result (received AFTER dispatch, or a new result event since
        # dispatch) still completes the comment, while a genuinely stale /
        # replayed result is still rejected. The dispatch stamps started_at +
        # result_count_before alongside the marker, so this guard has real data.
        started_at = float(
            getattr(comment, 'kato_run_started_at_epoch', 0.0) or 0.0,
        )
        raw_count = getattr(comment, 'kato_run_result_count_before', -1)
        try:
            result_count_before = int(raw_count)
        except (TypeError, ValueError):
            result_count_before = -1

        # Backwards compatibility for comments that were already
        # IN_PROGRESS before this marker existed: let the old idle guard
        # decide rather than wedging them forever after deploy.
        if started_at <= 0.0 and result_count_before < 0:
            return True

        if (
            started_at > 0.0
            and result_received_at_epoch > 0.0
        ):
            return result_received_at_epoch > started_at

        if result_count_before >= 0:
            return self._task_result_count(task_id) > result_count_before
        return True

    def _task_has_busy_turn(self, task_id: str) -> bool:
        """True when the live streaming session has any work in flight.

        "In flight" covers TWO states the dispatch path must treat as
        busy, because each one used to let a queued comment slip into
        a turn it didn't own and then be marked ADDRESSED by that
        turn's RESULT:

        1. Mid-turn (``is_working``): Claude has spoken at least one
           event for the current message but no RESULT yet.
        2. Sent-but-unacked: ``send_user_message`` has written to the
           CLI's stdin but Claude has not yet emitted its first event
           for that message. ``is_working`` walks ``_recent_events``,
           so during this race window it returns False even though
           there is a queued message waiting to be processed. Without
           this second check, a comment dispatched in that gap would
           fire its OWN ``send_user_message`` onto a "false-idle"
           session, and the PRIOR message's RESULT would then mark the
           comment ``ADDRESSED`` before its work had even started
           (visible symptom: kato's reply quoted prior-turn work and
           the chat panel was still ``thinking`` on the comment).
        """
        if self._session_manager is None:
            return False
        try:
            session = self._session_manager.get_session(task_id)
        except Exception:
            return False
        if session is None or not getattr(session, 'is_alive', False):
            return False
        if bool(getattr(session, 'is_working', False)):
            return True
        sent = int(getattr(session, 'user_messages_sent', 0) or 0)
        received = int(getattr(session, 'result_events_received', 0) or 0)
        return sent > received

    def _task_session_is_stalled(self, task_id: str) -> bool:
        """True when the task's session is alive but no longer processing input.

        A stalled session has a sent user message that never produced a
        ``result`` (``user_messages_sent > result_events_received``),
        is NOT actively mid-turn (``is_working`` is False), and the last
        send was longer ago than ``_COMMENT_SEND_ACK_GRACE_SECONDS``.
        That combination means the subprocess is alive but its turn loop
        has ended — writing another ``send_user_message`` would vanish
        into the void. ``_task_has_busy_turn`` reports such a session as
        busy (``sent > received``), which is what kept queued comments
        ``pending`` forever; dispatch uses this to age that gap out and
        force a fresh respawn instead. Deliberately conservative: an
        unknown last-send time (``0``) is NOT treated as stalled.
        """
        if self._session_manager is None:
            return False
        try:
            session = self._session_manager.get_session(task_id)
        except Exception:
            return False
        if session is None or not getattr(session, 'is_alive', False):
            return False
        if bool(getattr(session, 'is_working', False)):
            return False
        sent = int(getattr(session, 'user_messages_sent', 0) or 0)
        received = int(getattr(session, 'result_events_received', 0) or 0)
        if sent <= received:
            return False
        last_sent = float(
            getattr(session, 'last_user_message_sent_epoch', 0.0) or 0.0,
        )
        if last_sent <= 0:
            return False
        return (time.time() - last_sent) >= _COMMENT_SEND_ACK_GRACE_SECONDS

    def _run_comment_agent(
        self,
        task_id: str,
        record,
        force_respawn: bool = False,
        run_marker: str = '',
    ) -> bool:
        """Hand the comment off to the streaming session as a user message.

        Sends the prompt into the live chat session when one exists and
        is healthy; otherwise (no session, dead session, or — when
        ``force_respawn`` is set — a stalled session that won't consume
        stdin) respawns Claude so the comment actually runs. The
        operator workflow is "comment lands → kato works on it".

        ``force_respawn`` is set by the dispatcher when the alive
        session is stalled: we terminate the dead-but-alive subprocess
        first so the session manager spawns a genuinely fresh one
        (``start_session`` returns the existing session untouched while
        it is still ``is_alive``), preserving the ``--resume`` id on the
        record so conversation history carries over.
        """
        prompt = self._comment_agent_prompt(
            task_id, record, run_marker=run_marker,
        )
        if self._session_manager is None:
            return self._spawn_comment_agent(task_id, record, prompt)
        session = self._session_manager.get_session(task_id)
        if session is None or not getattr(session, 'is_alive', False):
            return self._spawn_comment_agent(task_id, record, prompt)
        if force_respawn:
            self._terminate_stalled_session(task_id)
            return self._spawn_comment_agent(task_id, record, prompt)
        send = getattr(session, 'send_user_message', None)
        if not callable(send):
            return False
        send(prompt)
        return True

    def _terminate_stalled_session(self, task_id: str) -> None:
        """Kill a stalled-but-alive subprocess so a fresh one can spawn.

        Keeps the session RECORD (``remove_record=False``) so the
        respawn can still ``--resume`` the prior conversation id.
        Best-effort: a failure here just means the respawn may reuse the
        stalled session, which is no worse than before.
        """
        if self._session_manager is None:
            return
        terminate = getattr(self._session_manager, 'terminate_session', None)
        if not callable(terminate):
            return
        try:
            terminate(task_id, remove_record=False)
            self.logger.info(
                'terminated stalled session for task %s before respawn',
                task_id,
            )
        except Exception:
            self.logger.exception(
                'failed to terminate stalled session for task %s', task_id,
            )

    def _warn_if_comment_has_no_resumable_session(self, task_id: str, record) -> None:
        """Flag a comment respawn that will carry ZERO prior conversation.

        The respawn path (``resume_session_for_chat``) already resumes via
        the task's persisted ``agent_session_id`` whenever one is on file —
        this only covers the one case that's genuinely a context loss: no
        record, or a record with no session id, meaning the agent that
        answers this comment has never seen the task's implementation
        history at all. Diagnostic only — never blocks the run — but a
        report of kato "not aware of what happened before" should show up
        HERE in the logs, distinguishable from a resumed-but-under-specified
        prompt (the case the snippet/guardrail above actually fixes).
        """
        if self._session_manager is None:
            return
        try:
            record_on_file = self._session_manager.get_record(task_id)
        except Exception:
            return
        if record_on_file is not None and getattr(record_on_file, 'agent_session_id', ''):
            return
        self.logger.warning(
            'comment %s on task %s: no prior agent session on file — this '
            'respawn starts with NO conversation history from the task\'s '
            'implementation or earlier comments',
            getattr(record, 'id', '<unknown>'), task_id,
        )

    def _spawn_comment_agent(self, task_id: str, record, prompt: str) -> bool:
        """Respawn Claude for a queued local diff comment when no subprocess is alive."""
        runner = self._planning_session_runner
        if runner is None:
            # The prime "Claude is idle, not working on my comment"
            # cause: nothing can respawn the session, so the comment
            # ping-pongs QUEUED↔IN_PROGRESS every scan tick forever.
            # Make it loud instead of a silent False.
            self.logger.warning(
                'comment %s on task %s cannot start: no planning session '
                'runner wired — Claude will stay idle until a session is '
                'spawned another way',
                getattr(record, 'id', '<unknown>'), task_id,
            )
            return False
        self._warn_if_comment_has_no_resumable_session(task_id, record)
        cwd = self._comment_agent_cwd(task_id, record)
        summary = ''
        description = ''
        workspace_root = ''
        if self._workspace_manager is not None:
            workspace = self._workspace_manager.get(task_id)
            summary = str(getattr(workspace, 'task_summary', '') or '')
            description = str(getattr(workspace, 'task_description', '') or '')
            # Task folder: scopes the prompt boundary and the docker mount.
            try:
                workspace_root = str(
                    self._workspace_manager.workspace_path(task_id) or '',
                )
            except Exception:
                workspace_root = ''
        # Expose the task's OTHER repo clones too. Without this a
        # comment-driven respawn spawned a single-repo session that
        # couldn't read across repos (the cross-repo "that repo is
        # forbidden" refusal) and made every sibling-repo path look
        # outside the sandbox. Mirrors the chat-send route's --add-dir set.
        additional_dirs = sibling_repository_dirs(
            self._workspace_manager, task_id,
        )
        self.logger.info(
            'comment %s on task %s: respawning Claude to work on it '
            '(cwd=%s, +%d repo(s))',
            getattr(record, 'id', '<unknown>'), task_id, cwd or '<none>',
            len(additional_dirs),
        )
        runner.resume_session_for_chat(
            task_id=task_id,
            message=prompt,
            cwd=cwd,
            task_summary=summary,
            task_description=description,
            workspace_root=workspace_root,
            additional_dirs=additional_dirs,
        )
        return True

    def _comment_agent_cwd(self, task_id: str, record) -> str:
        """Prefer the commented repo clone, fallback to another repo
        clone in the same task.

        Deliberately never falls back to the bare task workspace
        folder (``workspace_path(task_id)``) except as a last resort
        with zero repos to fall back to: sandbox_scope's
        ``_effective_roots`` widens the sandbox ONE level up from
        ``cwd`` on the assumption ``cwd`` is ``<workspaces>/<task_id>/
        <repo>``. A comment with a blank/unresolvable ``repo_id`` (a
        real, reachable case — ``CommentRecord.repo_id`` defaults to
        ``''``) used to fall back to the task folder ITSELF, which is
        already one level up — so the widening then added
        ``dirname(<task_id folder>)``, i.e. the ENTIRE workspaces root
        shared by every task, silently making every other task's files
        read as "inside this session's sandbox".
        """
        if self._workspace_manager is None:
            return ''
        repo_id = str(getattr(record, 'repo_id', '') or '').strip()
        if repo_id:
            try:
                return str(self._workspace_manager.repository_path(task_id, repo_id))
            except Exception:
                pass
        try:
            workspace = self._workspace_manager.get(task_id)
            repo_ids = getattr(workspace, 'repository_ids', None)
            if isinstance(repo_ids, list) and repo_ids:
                fallback_repo_id = str(repo_ids[0] or '').strip()
                if fallback_repo_id:
                    return str(
                        self._workspace_manager.repository_path(task_id, fallback_repo_id),
                    )
        except Exception:
            pass
        try:
            return str(self._workspace_manager.workspace_path(task_id))
        except Exception:
            return ''

    def _comment_agent_prompt(self, task_id, record, run_marker: str = '') -> str:
        # Local import to match how the rest of this module defers
        # review_comment_utils; same constant the review path filters with.
        from kato_core_lib.helpers.review_comment_utils import (
            KATO_SELF_REPLY_PREFIXES,
        )

        body = str(getattr(record, 'body', '') or '')
        # ONE interface builds the payload every comment surface needs —
        # where the comment is, the code actually there, the prior turns, and
        # how far to go. Assembling those by hand per builder is what let
        # pieces go missing: a prompt with no code turned "revert this" into a
        # whole-file rewrite, and a thread that kept kato's own replies fed
        # them back as if a human had written them.
        #
        # Only the PRODUCT text stays here — the thread header and the
        # Claude/Operator speaker labels are kato's, and a core-lib must not
        # carry them.
        context = build_comment_prompt_context(
            record,
            workspace_path=self._comment_agent_cwd(task_id, record),
            wrap=wrap_untrusted_workspace_content,
            missing_location_label='File: (no file specified)',
            guardrail_purpose='to address this comment',
            thread=CommentThreadSpec(
                entries=tuple(
                    self._comment_thread_replies(task_id, getattr(record, 'id', '')),
                ),
                header=(
                    '\n\nThread so far (oldest to newest) — address the '
                    'LATEST operator reply, which supersedes earlier turns:\n'
                ),
                label_for=lambda reply: (
                    'Claude' if str(getattr(reply, 'author', '')) == 'claude'
                    else 'Operator'
                ),
                drop_prefixes=KATO_SELF_REPLY_PREFIXES,
            ),
        )
        marker_instruction = ''
        marker = str(run_marker or '').strip()
        if marker:
            marker_instruction = (
                '\nEnd your final response with this exact marker on its '
                f'own final line: {marker}'
            )
        return (
            'Operator-added review comment from the kato diff tab.\n\n'
            f'{context.location}'
            f'{context.code}'
            f'Comment: {body}'
            f'{context.thread}\n\n'
            'Address this comment. If a code change is needed:\n'
            f'{context.guardrails}'
            'Commit the fix on the current task branch. Your final '
            'response is copied into this comment thread as Claude\'s '
            'reply, so write it directly to the reviewer. If the comment '
            'is a question rather than a fix request, answer the question '
            f'without committing.{marker_instruction}'
        )

    def _comment_dispatch_lock_for(self, task_id: str):
        """Return the per-task lock that serializes comment dispatch."""
        with self._comment_dispatch_locks_lock:
            lock = self._comment_dispatch_locks.get(task_id)
            if lock is None:
                lock = threading.Lock()
                self._comment_dispatch_locks[task_id] = lock
            return lock

    def has_local_comment_in_progress(self, task_id: str) -> bool:
        """True when a local diff-comment agent run currently owns this task's
        workspace clone. The review-comment dispatch (which runs through the
        parallel runner's per-task key) checks this so it never starts a
        review-fix batch while a local comment agent is mid-run on the SAME
        on-disk checkout — the two dispatch paths otherwise use different
        locks (see ``_task_has_active_local_comment`` in the scan job)."""
        store = self._comment_store_for(task_id)
        if store is None:
            return False
        try:
            return self._task_has_in_progress_comment(store)
        except Exception:
            return False

    def _comment_expects_answer_only(self, task_id: str, comment) -> bool:
        """True when a local diff comment should be answered, not fixed."""
        from kato_core_lib.helpers.review_comment_utils import is_question_comment

        target = comment
        for reply in self._comment_thread_replies(task_id, getattr(comment, 'id', '')):
            if str(getattr(reply, 'author', '')) != 'claude':
                target = reply
        return is_question_comment(target)

    def _add_comment_agent_reply(self, store, comment, result_text: str) -> None:
        """Mirror Claude's final answer back into the comment thread."""
        body = str(result_text or '').strip()
        if not body:
            return
        from kato_core_lib.comment_core_lib import (
            CommentRecord,
            CommentSource,
        )

        try:
            store.add(CommentRecord(
                repo_id=str(getattr(comment, 'repo_id', '') or '').strip(),
                file_path=str(getattr(comment, 'file_path', '') or '').strip(),
                line=int(getattr(comment, 'line', -1) or -1),
                parent_id=str(getattr(comment, 'id', '') or '').strip(),
                author='claude',
                body=body,
                source=CommentSource.LOCAL.value,
            ))
        except Exception:
            self.logger.exception(
                'failed to add Claude reply for comment %s',
                getattr(comment, 'id', '<unknown>'),
            )

    def _comment_thread_replies(self, task_id, root_id: str) -> list:
        """Replies in the thread rooted at ``root_id``, oldest first.

        Walks each comment's parent chain to find which thread it belongs
        to, so a reply-to-a-reply still resolves to the right root. Excludes
        the root itself. Best-effort: a store failure yields no replies.
        """
        root_id = str(root_id or '')
        if not root_id:
            return []
        store = self._comment_store_for(task_id)
        if store is None:
            return []
        try:
            comments = list(store.list())
        except Exception:
            return []
        by_id = {c.id: c for c in comments}

        def root_of(comment):
            seen = set()
            cur = comment
            while cur.parent_id and cur.parent_id in by_id and cur.id not in seen:
                seen.add(cur.id)
                cur = by_id[cur.parent_id]
            return cur.id

        replies = [c for c in comments if c.id != root_id and root_of(c) == root_id]
        replies.sort(key=lambda c: float(getattr(c, 'created_at_epoch', 0) or 0))
        return replies

    def _workspace_records(self) -> list:
        """Every workspace record, or ``[]`` — never raises.

        The comment scan walks these on every tick; a workspace manager that
        blows up must not take the whole tick with it.
        """
        if self._cleanup_service is None:
            return []
        return self._cleanup_service.list_workspaces()
