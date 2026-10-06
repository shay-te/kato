"""Kato's side of the review loop: its chat, its reviewer, its rules, its logs.

``review_loop_core_lib`` runs the loop — review, post findings, wait for the
fix, review again — and knows nothing about kato. This module implements the
lib's ports with kato's own machinery and builds the service:

* the chat is the task's main chat, reached through the SAME
  ``MainChatDelivery`` the comment runs use, so the two can never both send on
  one idle moment, and the operator's queued diff comments go first;
* the reviewer is a fresh, read-only run of the configured agent backend over
  every repository of the task (``ImplementationService.investigate``);
* a chat in Plan or Explain mode cannot apply fixes, so it refuses the loop;
* every step is a ``Mission <task>:`` log line, which the status feed turns
  into notifications.
"""

from __future__ import annotations

import threading
from contextlib import AbstractContextManager
from pathlib import Path

from review_loop_core_lib.review_loop_core_lib.data.state import ReviewLoopState
from review_loop_core_lib.review_loop_core_lib.ports import (
    ChatReadiness,
    ChatTurnEnd,
    LoopWording,
)
from review_loop_core_lib.review_loop_core_lib.runner import RunnerOptions
from review_loop_core_lib.review_loop_core_lib.service import ReviewLoopService
from review_loop_core_lib.review_loop_core_lib.store import ReviewLoopStore
from sandbox_core_lib.sandbox_core_lib.workspace_delimiter import (
    wrap_untrusted_workspace_content,
)

from kato_core_lib.helpers.explain_mode_utils import is_explain_mode
from kato_core_lib.helpers.kato_paths_utils import kato_home_path
from kato_core_lib.helpers.late_binding import provider_for
from kato_core_lib.helpers.mission_logging_utils import log_mission_step
from kato_core_lib.helpers.plan_mode_store import PLAN_MODE, task_permission_mode
from kato_core_lib.helpers.planning_hold_store import held_permission_mode
from kato_core_lib.helpers.review_loop_guidance import (
    REVIEW_LOOP_FINDINGS_HEADER,
    REVIEW_LOOP_FIXER_GUIDANCE,
    REVIEW_LOOP_REVIEWER_GUIDANCE,
)
from kato_core_lib.helpers.workspace_repo_utils import (
    resolve_session_cwd,
    sibling_repository_dirs,
    task_repository_clones,
    task_workspace_root,
)

REVIEW_LOOPS_DIR_ENV = 'KATO_REVIEW_LOOPS_DIR'
_RESTART_REASON = 'kato restarted while the loop was running'
_SHUTDOWN_REASON = 'kato shut down while the loop was running'


def review_loops_root() -> Path:
    """``~/.kato/review_loops`` — outside every repository clone, so nothing
    the loop saves can be committed or scanned as part of a workspace."""
    return kato_home_path('review_loops', env_key=REVIEW_LOOPS_DIR_ENV)


def chat_mode_refusal(task_id: str) -> str:
    """Why the task's chat cannot apply fixes right now, or ''.

    Read the same way every spawn reads it: the ``kato:wait-planning`` hold
    outranks the operator's mode pick.
    """
    mode = held_permission_mode(task_id, task_permission_mode(task_id))
    if mode == PLAN_MODE:
        return 'the chat is in Plan mode — the fixes need a chat that can edit files'
    if is_explain_mode(mode):
        return 'the chat is in Explain mode — the fixes need a chat that can edit files'
    return ''


class KatoChatChannel(object):
    """The task's main chat, as the review loop sees it."""

    def __init__(self, *, comment_runs, session_manager, workspace_manager) -> None:
        self._get_comment_runs = provider_for(comment_runs)
        self._get_session_manager = provider_for(session_manager)
        self._get_workspace_manager = provider_for(workspace_manager)

    @property
    def _delivery(self):
        return self._get_comment_runs().chat_delivery

    def readiness(self, task_id: str) -> ChatReadiness:
        refusal = chat_mode_refusal(task_id)
        if refusal:
            return ChatReadiness.refuse(refusal)
        delivery = self._delivery
        if delivery.runner_in_flight(task_id):
            return ChatReadiness.wait('kato is running this task in the background')
        if self._get_comment_runs().has_local_comment_pending(task_id):
            return ChatReadiness.wait('a diff comment goes first')
        if delivery.has_busy_turn(task_id) and not delivery.is_stalled(task_id):
            return ChatReadiness.wait('the agent is mid-turn')
        return ChatReadiness.ready()

    def dispatch_lock(self, task_id: str) -> AbstractContextManager:
        return self._delivery.dispatch_lock_for(task_id)

    def is_stalled(self, task_id: str) -> bool:
        return self._delivery.is_stalled(task_id)

    def session_alive(self, task_id: str) -> bool:
        manager = self._get_session_manager()
        if manager is None:
            return False
        try:
            session = manager.get_session(task_id)
        except Exception:
            return False
        return bool(session is not None and getattr(session, 'is_alive', False))

    def pending_approval(self, task_id: str) -> str:
        """The tool the task's chat waits on the operator to approve, or ''.

        Read from the session's LIVE pending-request list — the same source
        the approval card and the orange tab use — so the loop's "waiting for
        your approval" appears and clears exactly when they do.
        """
        manager = self._get_session_manager()
        if manager is None:
            return ''
        try:
            session = manager.get_session(task_id)
            read = getattr(session, 'pending_control_request_tool', None)
            return str(read() or '') if callable(read) else ''
        except Exception:
            return ''

    def deliver(self, task_id: str, prompt: str, *, force_respawn: bool) -> bool:
        return self._delivery.deliver(
            task_id,
            prompt,
            label='review loop',
            cwd_for=lambda: self._chat_cwd(task_id),
            force_respawn=force_respawn,
        )

    def turn_end_since(self, task_id: str, since_epoch: float) -> ChatTurnEnd | None:
        turn = self._delivery.turn_end_since(task_id, since_epoch)
        if turn is None:
            return None
        return ChatTurnEnd(received_at=turn.received_at, is_error=turn.is_error, text=turn.text)

    def _chat_cwd(self, task_id: str) -> str:
        """The repo clone a respawned chat runs in — the one it last used."""
        manager = self._get_session_manager()
        record_cwd = ''
        if manager is not None:
            try:
                record = manager.get_record(task_id)
            except Exception:
                record = None
            record_cwd = str(getattr(record, 'cwd', '') or '')
        return resolve_session_cwd(self._get_workspace_manager(), task_id, record_cwd)


class KatoReviewer(object):
    """A fresh, read-only review of one task by the configured agent backend."""

    def __init__(self, *, implementation_service, workspace_manager) -> None:
        self._get_implementation_service = provider_for(implementation_service)
        self._get_workspace_manager = provider_for(workspace_manager)

    def review(self, prompt: str, *, task_id: str, cancel_event: threading.Event) -> str:
        workspace_manager = self._get_workspace_manager()
        clones = task_repository_clones(workspace_manager, task_id)
        return self._get_implementation_service().investigate(
            prompt,
            # In a repo clone, never the bare task folder (see
            # ``resolve_session_cwd``); the task folder is reachable through
            # ``additional_dirs`` and, in docker mode, the mount.
            cwd=clones[0] if clones else '',
            additional_dirs=sibling_repository_dirs(workspace_manager, task_id),
            sandbox_root=task_workspace_root(workspace_manager, task_id),
            task_id=task_id,
            log_label=f'{task_id} review loop',
            cancel_event=cancel_event,
        )


def review_loop_refusal(task_id: str, *, implementation_service, workspace_manager) -> str:
    """Why a loop cannot start for ``task_id`` right now, or ''."""
    if not getattr(implementation_service, 'supports_investigation', False):
        return 'the configured agent backend cannot run an independent review (Claude or Codex can)'
    if not task_repository_clones(workspace_manager, task_id):
        return 'this task has no repository clone to review'
    return chat_mode_refusal(task_id)


def log_review_loop_event(logger, state: ReviewLoopState, event: str) -> None:
    """One ``Mission <task>:`` line per loop step — the status feed (and its
    notifications) read these."""
    message = _event_message(state, event)
    if message:
        log_mission_step(logger, state.task_id, message)


def _event_message(state: ReviewLoopState, event: str) -> str:
    number = state.round
    if event == 'started':
        return f'review loop started (up to {state.max_rounds} reviews)'
    if event == 'reviewing':
        return f'review loop round {number}: reviewing the whole change'
    if event == 'reviewed':
        counts = state.current_round.counts
        return (
            f'review loop round {number}: {counts["BLOCKER"]} blocker, '
            f'{counts["MAJOR"]} major, {counts["MINOR"]} minor, {counts["NIT"]} nit'
        )
    if event == 'sent':
        return f'review loop round {number}: findings sent to the chat'
    if event == 'fixed':
        return f'review loop round {number}: the chat finished its fixes'
    if event == 'finished':
        return (
            f'review loop finished ({state.status.value}) after {number} '
            f'round(s): {state.reason}'
        )
    return ''


def build_review_loop_service(
    *,
    comment_runs,
    session_manager,
    workspace_manager,
    implementation_service,
    logger,
    options: RunnerOptions | None = None,
) -> ReviewLoopService:
    """The process's one review-loop service, wired to kato.

    Collaborators may be ``later(...)`` markers: each is resolved per call, so
    a manager rebuilt afterwards (setup mode) is the one the loop talks to.
    ``options`` (the loop's timings) defaults to the lib's real-use values.
    """
    get_implementation_service = provider_for(implementation_service)
    get_workspace_manager = provider_for(workspace_manager)
    get_logger = provider_for(logger)
    return ReviewLoopService(
        store=ReviewLoopStore(review_loops_root(), logger=get_logger()),
        chat=KatoChatChannel(
            comment_runs=comment_runs,
            session_manager=session_manager,
            workspace_manager=workspace_manager,
        ),
        reviewer=KatoReviewer(
            implementation_service=implementation_service,
            workspace_manager=workspace_manager,
        ),
        wording=LoopWording(
            wrap_untrusted=lambda text, source: wrap_untrusted_workspace_content(
                text, source_path=source,
            ),
            findings_header=REVIEW_LOOP_FINDINGS_HEADER,
            reviewer_guidance=REVIEW_LOOP_REVIEWER_GUIDANCE,
            findings_guidance=REVIEW_LOOP_FIXER_GUIDANCE,
        ),
        observer=lambda state, event: log_review_loop_event(get_logger(), state, event),
        can_start=lambda task_id: review_loop_refusal(
            task_id,
            implementation_service=get_implementation_service(),
            workspace_manager=get_workspace_manager(),
        ),
        options=options,
        logger=get_logger(),
    )


def mark_interrupted_review_loops(service: ReviewLoopService) -> int:
    """Boot: close loops the previous kato process left running."""
    return service.mark_interrupted(_RESTART_REASON)


def shut_down_review_loops(service: ReviewLoopService) -> None:
    service.shutdown(_SHUTDOWN_REASON)

