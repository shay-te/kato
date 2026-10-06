"""One way for kato to put a message into a task's main chat.

Kato writes into a task's chat on its own behalf — a queued operator comment,
an automated review's findings — and every such send has the same hazards:

* **The chat may be busy.** A turn in flight, or a message written to stdin
  that the CLI has not acknowledged yet, means a second send lands in the
  SAME turn and its reply gets credited to the wrong request.
* **The session may be gone or stalled.** Dead → respawn with ``--resume``;
  alive but no longer reading stdin → terminate and respawn, or the message
  vanishes.
* **Two senders may reach the same idle moment.** Each passes the busy check
  before either has sent. One per-task lock, held across check → send by
  EVERY sender, is what makes that impossible — which is why there is one
  instance of this class per process, owned by the comment-run service and
  shared with anything else that sends.

This was the session half of the comment-run service; it lives here so a
second sender reuses it instead of copying it.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable

from claude_core_lib.claude_core_lib.session.streaming import (
    TURN_ACK_GRACE_SECONDS as SEND_ACK_GRACE_SECONDS,
)

from kato_core_lib.helpers.late_binding import provider_for
from kato_core_lib.helpers.logging_utils import configure_logger
from kato_core_lib.helpers.workspace_repo_utils import sibling_repository_dirs


@dataclass(frozen=True)
class TurnEnd(object):
    """A finished turn of a task's chat: when, whether it failed, its text."""

    received_at: float
    is_error: bool
    text: str


class MainChatDelivery(object):
    """Busy/stalled checks, the per-task send lock, and send-or-respawn."""

    def __init__(
        self,
        *,
        session_manager=None,
        workspace_manager=None,
        parallel_task_runner=None,
        planning_session_runner=None,
        logger=None,
    ) -> None:
        self._get_session_manager = provider_for(session_manager)
        self._get_workspace_manager = provider_for(workspace_manager)
        self._get_parallel_task_runner = provider_for(parallel_task_runner)
        self._get_planning_session_runner = provider_for(planning_session_runner)
        self._logger_getter = provider_for(
            logger if logger is not None else configure_logger('MainChatDelivery'),
        )
        self._dispatch_locks: dict[str, threading.Lock] = {}
        self._dispatch_locks_lock = threading.Lock()

    @property
    def logger(self):
        """The host's CURRENT logger — resolved per call, never captured."""
        return self._logger_getter()

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

    def dispatch_lock_for(self, task_id: str) -> threading.Lock:
        """The per-task lock every kato-authored send into the chat holds.

        One lock per task, shared by every sender, so the busy-check → send
        sequence is atomic across them: two senders reaching the same idle
        moment can never both pass the check and both send.
        """
        with self._dispatch_locks_lock:
            lock = self._dispatch_locks.get(task_id)
            if lock is None:
                lock = threading.Lock()
                self._dispatch_locks[task_id] = lock
            return lock

    def runner_in_flight(self, task_id: str) -> bool:
        """True while the parallel runner owns this task's workspace.

        A review-fix batch (or the task's implementation run) may be actively
        operating the task's clones through the runner, which uses a DIFFERENT
        lock than ``dispatch_lock_for``. Starting a chat run now would run
        concurrent git ops on the same checkout, so senders stay put until the
        runner frees the task.
        """
        runner = self._parallel_task_runner
        if runner is None:
            return False
        try:
            return bool(runner.is_in_flight(task_id))
        except Exception:
            return False

    def has_busy_turn(self, task_id: str) -> bool:
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

    def is_stalled(self, task_id: str) -> bool:
        """True when the task's session is alive but no longer processing input.

        A stalled session has a sent user message that never produced a
        ``result`` (``user_messages_sent > result_events_received``),
        is NOT actively mid-turn (``is_working`` is False), and the last
        send was longer ago than ``SEND_ACK_GRACE_SECONDS``.
        That combination means the subprocess is alive but its turn loop
        has ended — writing another ``send_user_message`` would vanish
        into the void. ``has_busy_turn`` reports such a session as
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
        return (time.time() - last_sent) >= SEND_ACK_GRACE_SECONDS

    def result_count(self, task_id: str) -> int:
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

    def deliver(
        self,
        task_id: str,
        prompt: str,
        *,
        label: str,
        cwd_for: Callable[[], str],
        force_respawn: bool = False,
    ) -> bool:
        """Hand ``prompt`` to the task's main chat as a user message.

        Sends it into the live chat session when one exists and is healthy;
        otherwise (no session, dead session, or — when ``force_respawn`` is
        set — a stalled session that won't consume stdin) respawns the chat so
        the work actually runs.

        ``force_respawn`` is set by a dispatcher when the alive session is
        stalled: the dead-but-alive subprocess is terminated first so the
        session manager spawns a genuinely fresh one (``start_session``
        returns the existing session untouched while it is still
        ``is_alive``), preserving the ``--resume`` id on the record so
        conversation history carries over.
        """
        if self._session_manager is None:
            return self.spawn(task_id, prompt, label=label, cwd_for=cwd_for)
        session = self._session_manager.get_session(task_id)
        if session is None or not getattr(session, 'is_alive', False):
            return self.spawn(task_id, prompt, label=label, cwd_for=cwd_for)
        if force_respawn:
            self.terminate_stalled(task_id)
            return self.spawn(task_id, prompt, label=label, cwd_for=cwd_for)
        send = getattr(session, 'send_user_message', None)
        if not callable(send):
            return False
        send(prompt)
        return True

    def spawn(
        self, task_id: str, prompt: str, *, label: str, cwd_for: Callable[[], str],
    ) -> bool:
        """Respawn the task's chat with ``prompt`` when no subprocess is alive.

        ``label`` names the work in the log lines (``"comment <id>"``);
        ``cwd_for`` resolves the working directory only when a spawn actually
        happens.
        """
        runner = self._planning_session_runner
        if runner is None:
            # The prime "Claude is idle, not working on my comment"
            # cause: nothing can respawn the session, so the comment
            # ping-pongs QUEUED↔IN_PROGRESS every scan tick forever.
            # Make it loud instead of a silent False.
            self.logger.warning(
                '%s on task %s cannot start: no planning session '
                'runner wired — Claude will stay idle until a session is '
                'spawned another way',
                label, task_id,
            )
            return False
        self._warn_if_no_resumable_session(task_id, label)
        cwd = cwd_for()
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
            '%s on task %s: respawning Claude to work on it '
            '(cwd=%s, +%d repo(s))',
            label, task_id, cwd or '<none>',
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

    def terminate_stalled(self, task_id: str) -> None:
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

    def _warn_if_no_resumable_session(self, task_id: str, label: str) -> None:
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
            '%s on task %s: no prior agent session on file — this '
            'respawn starts with NO conversation history from the task\'s '
            'implementation or earlier comments',
            label, task_id,
        )

    def turn_end_since(self, task_id: str, since_epoch: float) -> TurnEnd | None:
        """The task chat's newest finished turn after ``since_epoch``, if any.

        Reads the session's event history without consuming it — the live
        event queue has exactly one reader, and a second one would steal the
        chat's own events. Backend-neutral: it keys off ``is_terminal`` and
        ``received_at_epoch``, which every transport's events carry.
        """
        if self._session_manager is None:
            return None
        try:
            session = self._session_manager.get_session(task_id)
            events = list(session.recent_events() or []) if session is not None else []
        except Exception:
            return None
        for event in reversed(events):
            if not getattr(event, 'is_terminal', False):
                continue
            received = float(getattr(event, 'received_at_epoch', 0.0) or 0.0)
            if received <= since_epoch:
                return None
            raw = getattr(event, 'raw', None) or {}
            event_type = str(getattr(event, 'event_type', '') or '')
            return TurnEnd(
                received_at=received,
                is_error=bool(raw.get('is_error')) or event_type.endswith(('failed', 'aborted')),
                text=str(raw.get('result', '') or ''),
            )
        return None
