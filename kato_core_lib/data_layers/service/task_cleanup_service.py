"""Cleaning up after a task the operator is done with.

Workspaces, planning sessions, and agent conversations all outlive the task
that created them, and each is cleaned on a different signal: a ticket that
left the review queue, a session with no live process, a workspace older than
the review TTL. That decision — *which* of the things on disk is safe to touch
— is the whole job of this service.

The rule kato refuses to break: **nothing here ever auto-deletes**. A done
task's workspace has its status flipped to ``done`` (the UI greys the dot) and
its conversation released; only an operator-triggered delete removes anything.
A "cleanup" that wipes an in-review clone is the failure this code exists to
prevent, which is why every candidate is matched on a case-normalized id — a
case-sensitive comparison once wiped a task that was still in review.
"""

from __future__ import annotations

import time

from utils_core_lib.utils_core_lib.text_utils import normalized_lower_text

from kato_core_lib.data_layers.service.workspace_manager import (
    WORKSPACE_STATUS_ACTIVE,
    WORKSPACE_STATUS_DONE,
    WORKSPACE_STATUS_PROVISIONING,
    WORKSPACE_STATUS_REVIEW,
)
from kato_core_lib.helpers.late_binding import provider_for
from kato_core_lib.helpers.logging_utils import configure_logger


class TaskCleanupService(object):
    """Release what a finished task left behind — without deleting anything."""

    def __init__(
        self,
        *,
        task_service,
        session_manager=None,
        workspace_manager=None,
        implementation_service=None,
        state_registry=None,
        review_workspace_ttl_seconds: float = 3600.0,
        logger=None,
    ) -> None:
        self._get_task_service = provider_for(task_service)
        self._get_session_manager = provider_for(session_manager)
        self._get_workspace_manager = provider_for(workspace_manager)
        self._get_implementation_service = provider_for(implementation_service)
        self._get_state_registry = provider_for(state_registry)
        self._get_review_ttl = provider_for(review_workspace_ttl_seconds)
        self._logger_getter = provider_for(
            logger if logger is not None else configure_logger('TaskCleanupService'),
        )

    @property
    def logger(self):
        """The host's CURRENT logger — resolved per call, never captured."""
        return self._logger_getter()

    @property
    def _task_service(self):
        return self._get_task_service()

    @property
    def _session_manager(self):
        return self._get_session_manager()

    @property
    def _workspace_manager(self):
        return self._get_workspace_manager()

    @property
    def _implementation_service(self):
        return self._get_implementation_service()

    @property
    def _state_registry(self):
        return self._get_state_registry()

    @property
    def _review_workspace_ttl_seconds(self) -> float:
        return self._get_review_ttl()

    def cleanup_done_tasks(self) -> None:
        """Public boot entrypoint for the done-task prune.

        Called once at startup (before the planning webserver starts
        serving tabs) so a restart never resurrects a tab for a task
        whose ticket already moved to done/closed. Without this, a
        stale ``~/.kato/sessions/<id>.json`` left on disk renders as
        a tab on boot and only disappears on the first scan-tick
        cleanup ~30s later — the "task is back after restart" bug.
        Best-effort: the underlying cleanup already swallows its own
        per-source failures.
        """
        self.cleanup_done_task_conversations()

    def cleanup_done_task_conversations(self) -> None:
        """Delete conversation containers for tasks no longer in the review state.

        When a reviewer merges a PR and moves the task to done, Kato detects
        it is missing from the review-task list and removes the associated
        agent-server container to avoid accumulation.
        """
        try:
            current_review_norm = {
                normalized_lower_text(task.id)
                for task in self._task_service.get_review_tasks()
            }
        except Exception:
            self.logger.warning(
                'failed to fetch review tasks for conversation cleanup; skipping'
            )
            return

        stale_task_ids = {
            tid for tid in self._state_registry.tracked_task_ids()
            if normalized_lower_text(tid) not in current_review_norm
        }
        for task_id in stale_task_ids:
            for agent_session_id in self._state_registry.session_ids_for_task(task_id):
                self.logger.info(
                    'task %s is no longer in review; stopping conversation %s',
                    task_id,
                    agent_session_id,
                )
                try:
                    self._implementation_service.delete_conversation(agent_session_id)
                except Exception:
                    self.logger.warning(
                        'failed to stop conversation %s for done task %s',
                        agent_session_id,
                        task_id,
                    )
            self._state_registry.forget_task(task_id)

        self._cleanup_done_planning_sessions(current_review_norm)

    def _cleanup_done_planning_sessions(
        self,
        current_review_norm: set[str],
    ) -> None:
        """Mark planning-UI tabs whose ticket has moved to done/closed.

        Previous behaviour terminated the live subprocess, removed the
        persisted session record, AND deleted the workspace folder when
        a ticket left both Open and Review buckets. Operator policy is
        now NEVER auto-delete anything from disk — the workspace clone,
        the session record, and the tab all stay. Instead, the
        workspace status is flipped to ``done`` so the UI renders the
        status circle greyed-out; the operator decides when (or
        whether) to wipe the clone via the explicit DELETE endpoint.
        """
        if self._session_manager is None and self._workspace_manager is None:
            return
        try:
            assigned_norm = {
                normalized_lower_text(task.id)
                for task in self._task_service.get_assigned_tasks()
            }
        except Exception:
            self.logger.warning(
                'failed to fetch assigned tasks for session cleanup; '
                'leaving planning sessions in place this cycle'
            )
            return

        # All three id sources (platform / session records / workspace
        # folders) get normalized to a common case before comparison —
        # see ``normalized_lower_text``.
        live_norm = assigned_norm | current_review_norm
        # A task the operator DELETED is not "stale work to tidy up" — it
        # is gone. Touching it again, or narrating it every scan, is the
        # system refusing to accept an instruction it was already given.
        from kato_core_lib.helpers.forgotten_tasks_store import forgotten_task_ids
        forgotten = {normalized_lower_text(tid) for tid in forgotten_task_ids()}
        for task_id in self._stale_planning_task_ids(live_norm):
            if normalized_lower_text(task_id) in forgotten:
                continue
            # Log ONLY on the transition. This used to log unconditionally,
            # so every already-done workspace reprinted the same line every
            # scan — fifteen identical lines every three minutes, which is
            # how a log stops being read at all.
            if self._mark_workspace_done_silent(task_id):
                self.logger.info(
                    'task %s is no longer assigned or in review; '
                    'marking workspace as done (no delete — operator '
                    'must use the explicit DELETE endpoint)',
                    task_id,
                )

    def _stale_planning_task_ids(self, live_norm: set[str]) -> set[str]:
        """Task ids known to either manager that aren't live anymore.

        The ``active``/``provisioning`` in-flight guard exists for a
        narrow case: kato itself flips a ticket to *In Progress*
        while driving it, so it momentarily vanishes from both
        ``get_assigned_tasks()`` and ``get_review_tasks()``. Without
        a guard the next scan would wipe a workspace kato is mid-run
        on.

        BUT the workspace status is never reliably reset back from
        ``active`` once a task finishes, so an unconditional "active
        ⇒ never clean" rule shields a *done* task's leftover
        workspace forever — the tab never disappears (the
        "task-still-there-after-it's-done" bug). So an
        active/provisioning workspace is protected only when it's
        plausibly still being driven:

          * a live session subprocess exists for it, OR
          * it was updated within the grace window
            (``review_workspace_ttl_seconds``, 1h default — far
            longer than any single task run, operator-tunable).

        An active/provisioning workspace that is BOTH not live AND
        cold (no update within the grace) is a leftover: if its
        ticket isn't live either, it's stale and gets cleaned. When
        the TTL is 0 (operator disabled age-based cleanup) the
        legacy "protect all active/provisioning" behaviour is kept.

        Review-status workspaces are always protected: a ticket in
        the review / "To Verify" bucket with a local clone is work
        the operator may still be verifying, so its clone is kept
        until the ticket actually leaves the review bucket.
        """

        # norm-id -> ORIGINAL task id (first writer wins; the session
        # record id is preferred since that's the key the session
        # manager stored, so terminate/delete hit the right record).
        candidate_by_norm: dict[str, str] = {}

        def remember(task_id) -> str:
            norm = normalized_lower_text(task_id)
            candidate_by_norm.setdefault(norm, task_id)
            return norm

        if self._session_manager is not None:
            try:
                for record in self._session_manager.list_records():
                    remember(record.task_id)
            except Exception:
                self.logger.exception('failed to list planning session records')

        workspace_records = self.list_workspaces()
        protected_norm: set[str] = set()
        now_epoch = time.time()
        for record in workspace_records:
            norm = remember(record.task_id)
            bucket = self._classify_workspace_for_cleanup(record, now_epoch)
            if bucket == 'protected':
                protected_norm.add(norm)
            # 'stale' → no protection; falls through to the
            # live-norm subtraction below.
        stale_norm = set(candidate_by_norm) - live_norm - protected_norm
        return {candidate_by_norm[n] for n in stale_norm}

    def _classify_workspace_for_cleanup(self, record, now_epoch: float) -> str:
        """Bucket one workspace record for the stale sweep.

        Returns one of:
          * ``'protected'`` — keep the clone. Two cases:
              - an active/provisioning workspace that is plausibly
                still being driven (live session OR updated within
                the grace window OR TTL disabled); and
              - ANY review-state workspace. A ticket sitting in the
                review / "To Verify" bucket with a local clone is
                work the operator may still be reviewing — its clone
                is never deleted, regardless of age. (Previously a
                review clone older than the TTL was force-cleaned;
                that wiped clones for tickets the operator was still
                verifying — the "task disappeared while on verify"
                bug. Review clones are now kept until the ticket
                actually leaves the review bucket.)
          * ``'stale'`` — no special protection; the
            ``candidates - live_norm`` subtraction decides. The
            default for done/errored/terminated leftovers and for
            cold active/provisioning leftovers. Matching the
            pre-refactor fall-through: anything not explicitly
            protected here is cleaned iff its ticket isn't live.
        """
        status = getattr(record, 'status', '')
        ttl = self._review_workspace_ttl_seconds
        updated = float(getattr(record, 'updated_at_epoch', 0.0) or 0.0)
        if status in (WORKSPACE_STATUS_ACTIVE, WORKSPACE_STATUS_PROVISIONING):
            fresh = (
                ttl <= 0
                or updated <= 0
                or (now_epoch - updated) <= ttl
            )
            if self._has_live_session(record.task_id) or fresh:
                return 'protected'
            return 'stale'
        if status == WORKSPACE_STATUS_REVIEW:
            return 'protected'
        return 'stale'

    def list_workspaces(self) -> list:
        if self._workspace_manager is None:
            return []
        from kato_core_lib.helpers.lessons_path_utils import (
            is_reserved_workspace_dirname,
        )
        try:
            # Drop kato's own lessons-state dirs (lessons/ · lesson-candidates/)
            # that sit inside KATO_WORKSPACES_ROOT next to the task clones — they
            # are NOT tasks and must never be treated as workspaces (phantom
            # "lessons"/"lesson-candidates" tabs).
            return [
                record
                for record in self._workspace_manager.list_workspaces()
                if not is_reserved_workspace_dirname(getattr(record, 'task_id', ''))
            ]
        except Exception:
            self.logger.exception('failed to list workspaces')
            return []

    def _has_live_session(self, task_id) -> bool:
        if self._session_manager is None:
            return False
        try:
            session = self._session_manager.get_session(task_id)
        except Exception:
            return False
        return session is not None and getattr(session, 'is_alive', True)

    def _mark_workspace_done_silent(self, task_id: str) -> bool:
        """Flag a workspace as ``done`` without touching disk.

        Returns True only when the status actually CHANGED, so callers
        can log a transition rather than a steady state.

        Replaces the old ``_delete_workspace_silent`` because the
        operator policy is now NEVER auto-delete a workspace folder.
        The status flip is enough for the UI to render the tab's
        status circle greyed-out; the on-disk clone, the session
        record, and the tab itself all remain. The operator wipes
        the clone explicitly via the DELETE workspace endpoint
        when (and if) they want to.
        """
        if self._workspace_manager is None:
            return False
        update = getattr(self._workspace_manager, 'update_status', None)
        if not callable(update):
            return False
        # Already done? Nothing changed, so the caller must not announce a
        # transition that is not happening.
        try:
            record = self._workspace_manager.get(task_id)
            if str(getattr(record, 'status', '') or '') == WORKSPACE_STATUS_DONE:
                return False
        except Exception:
            pass
        try:
            update(task_id, WORKSPACE_STATUS_DONE)
        except Exception:
            self.logger.exception(
                'failed to mark workspace done for task %s', task_id,
            )
            return False
        return True

    def _delete_workspace_silent(self, _task_id: str) -> None:
        """Deprecated: kept as a no-op for backwards compatibility.

        Operator policy is NEVER auto-delete. Callers should use
        ``_mark_workspace_done_silent`` to flip the status instead.
        Direct callers of ``workspace_manager.delete`` should be
        operator-triggered only (the DELETE workspace endpoint).
        """
