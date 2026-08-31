"""Live Codex chats for the host, one per task.

Deliberately thin. The parts a session manager usually owns — the record
shape, where records live on disk, the naming and reload rules — are already
shared in ``agent_core_lib.session``; this adds only what is specific to
Codex: keeping the live per-task session objects and knowing that a Codex
chat's identity is a thread id learned from its first turn.

It is NOT a port of the Claude manager. That one carries machinery for
``claude --resume``: relocating a cwd-keyed JSONL transcript, killing leftover
processes still holding a session id, refusing a resume that silently started
a blank conversation. None of it has a Codex equivalent — ``codex exec resume``
takes the id as an argument and needs no file to be in the right place — so
copying that class would have meant carrying several hundred lines of
inapplicable defence.
"""

from __future__ import annotations

import threading
from pathlib import Path

from agent_core_lib.agent_core_lib.data.agent_backend import AgentBackend
from agent_core_lib.agent_core_lib.session.backend_chats import (
    record_accepts_backend,
)
from agent_core_lib.agent_core_lib.helpers.logging_utils import configure_logger
from agent_core_lib.agent_core_lib.session.record import (
    SESSION_STATUS_ACTIVE,
    SESSION_STATUS_TERMINATED,
    AgentSessionRecord,
)
from agent_core_lib.agent_core_lib.session.record_files import (
    record_key,
    write_record,
)
from codex_core_lib.codex_core_lib.session.streaming import StreamingCodexSession


class CodexSessionManager(object):
    """Start, find, and tear down live Codex chats."""

    #: Stamped onto every record this manager writes.
    AGENT_BACKEND = AgentBackend.CODEX.value

    def __init__(
        self,
        *,
        state_dir: str,
        session_factory=None,
        record_sink=None,
        record_source=None,
        logger=None,
    ) -> None:
        self._state_dir = Path(state_dir)
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._session_factory = session_factory or StreamingCodexSession
        # Called with each record this manager writes. The host keeps one
        # view of all chats regardless of backend, so a record written here
        # has to reach that view too — otherwise a Codex chat would not
        # appear in the chat list until the next reload.
        self._record_sink = record_sink
        # Called with a task id to fetch the record the host already holds.
        #
        # The write side alone was not enough. Without a way to READ, this
        # manager could not see an id an operator had adopted (so it spawned a
        # blank thread instead of resuming), and it rebuilt each record from
        # scratch on every spawn — silently dropping the task's parked chats,
        # its previous_session_ids and its context counters.
        self._record_source = record_source
        self.logger = logger or configure_logger('CodexSessionManager')
        self._sessions: dict[str, StreamingCodexSession] = {}
        self._lock = threading.RLock()

    # ----- sessions -------------------------------------------------------

    def get_session(self, task_id: str):
        """The live chat for ``task_id``, or ``None``.

        The liveness check happens OUTSIDE the lock, for the same reason it
        does in the Claude manager: a slow session must never hold a lock that
        unrelated callers — including the UI's git actions — wait on.
        """
        key = record_key(task_id)
        with self._lock:
            session = self._sessions.get(key)
        if session is None:
            return None
        if not session.is_alive:
            with self._lock:
                if self._sessions.get(key) is session:
                    del self._sessions[key]
            return None
        return session

    def start_session(
        self,
        *,
        task_id: str,
        cwd: str = '',
        binary: str = 'codex',
        model: str = '',
        task_summary: str = '',
        expected_branch: str = '',
        additional_dirs=(),
        initial_prompt: str = '',
        agent_session_id: str = '',
        **kwargs,
    ):
        """Return the task's live chat, starting one if needed.

        ``agent_session_id`` resumes an existing conversation — the id a
        previous run learned and persisted. Without it the first turn starts
        fresh and learns its own.
        """
        del kwargs  # accepted for parity with the other transports' managers
        key = record_key(task_id)
        previous = self._existing_record(task_id)
        # An explicit id wins; otherwise resume whatever this task already has
        # on file. Reading the record is what makes adoption work at all — the
        # operator pins an id through the UI, which writes the record and
        # nothing else, so a spawn that ignored the record would start a blank
        # thread and silently discard the conversation they just adopted.
        resume_id = str(agent_session_id or '').strip() or self._resume_id(previous)
        with self._lock:
            existing = self._sessions.get(key)
            if existing is not None and existing.is_alive:
                if initial_prompt:
                    existing.send_user_message(initial_prompt)
                return existing
            session = self._session_factory(
                task_id=str(task_id).strip(),
                cwd=cwd,
                binary=binary,
                model=model,
                agent_session_id=resume_id,
                additional_dirs=tuple(additional_dirs or ()),
                logger=self.logger,
            )
            self._sessions[key] = session
        record = self._record_for_spawn(
            previous,
            task_id=task_id,
            task_summary=task_summary,
            agent_session_id=resume_id,
            cwd=cwd,
            expected_branch=expected_branch,
        )
        self._persist(record)
        # The thread id only exists once the first turn has started, so the
        # record is written twice: now (so the chat is listed immediately)
        # and again when the id arrives. Without the second write the chat
        # could not be resumed after a restart.
        session._session_id_correction_callback = (
            lambda thread_id, r=record: self._record_session_id(r, thread_id)
        )
        session.start(initial_prompt=initial_prompt)
        return session

    def _existing_record(self, task_id: str):
        """The record the host already holds for ``task_id``, or ``None``."""
        if not callable(self._record_source):
            return None
        try:
            return self._record_source(task_id)
        except Exception:
            self.logger.exception(
                'failed to read the existing chat record for task %s', task_id,
            )
            return None

    def _resume_id(self, record) -> str:
        """The id to resume from ``record``, or '' to start fresh.

        Guarded by the shared ``record_accepts_backend``: a record sitting on
        another backend holds THAT agent's id, and an id only resolves in the
        CLI that issued it. Resuming from it would hand ``codex exec resume``
        a Claude session id, which finds nothing and opens blank.
        """
        if record is None or not record_accepts_backend(record, self.AGENT_BACKEND):
            return ''
        return str(getattr(record, 'agent_session_id', '') or '').strip()

    def _record_for_spawn(
        self,
        previous,
        *,
        task_id: str,
        task_summary: str,
        agent_session_id: str,
        cwd: str,
        expected_branch: str,
    ) -> AgentSessionRecord:
        """The record to persist for this spawn.

        UPDATES the existing record when there is one, rather than building a
        replacement. Persisting is a whole-file write, so returning a fresh
        object here erased everything not re-listed on it — the task's parked
        chats under other backends, its ``previous_session_ids``, and its
        context counters. That loss only surfaced at the next restart, as a
        Claude conversation missing from the chats menu.
        """
        if previous is not None and record_accepts_backend(
            previous, self.AGENT_BACKEND,
        ):
            previous.agent_backend = self.AGENT_BACKEND
            previous.agent_session_id = agent_session_id
            previous.status = SESSION_STATUS_ACTIVE
            previous.cwd = cwd
            previous.expected_branch = expected_branch
            if task_summary and not getattr(previous, 'task_summary', ''):
                previous.task_summary = task_summary
            return previous
        return AgentSessionRecord(
            task_id=str(task_id).strip(),
            task_summary=task_summary,
            agent_backend=self.AGENT_BACKEND,
            agent_session_id=agent_session_id,
            status=SESSION_STATUS_ACTIVE,
            cwd=cwd,
            expected_branch=expected_branch,
        )

    def terminate_session(self, task_id: str, *, remove_record: bool = False) -> None:
        key = record_key(task_id)
        with self._lock:
            session = self._sessions.pop(key, None)
        if session is not None:
            session.terminate()
        if remove_record:
            from agent_core_lib.agent_core_lib.session.record_files import (
                delete_record,
            )
            delete_record(self._state_dir, task_id, logger=self.logger)

    def shutdown(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            try:
                session.terminate()
            except Exception:
                self.logger.exception('failed to terminate a Codex chat')

    # ----- records --------------------------------------------------------

    def _record_session_id(self, record: AgentSessionRecord, thread_id: str) -> None:
        """Persist the id the first turn learned, so a restart can resume.

        Refuses when the record has moved to another backend. Switching the
        operator's chat parks this one — the record's backend changes and its
        id is cleared, but THIS subprocess is deliberately left running so
        switching back resumes it. Its first turn can then report a thread id
        after the switch, and writing that onto a record owned by another
        agent leaves the record claiming one id for two backends: the other
        backend's switch-back parks it under its own key, and a later resume
        hands a Codex thread id to a CLI that has never heard of it, which
        finds no transcript, starts blank, and looks like lost history.

        An EMPTY backend is a record written before backends were tracked —
        it belongs to whoever is running, so it is allowed.
        """
        if not record_accepts_backend(record, self.AGENT_BACKEND):
            self.logger.info(
                'task %s: not recording Codex chat id %s — the record is on '
                '%s now', record.task_id, thread_id,
                getattr(record, 'agent_backend', ''),
            )
            return
        record.agent_session_id = str(thread_id or '')
        self._persist(record)
        self.logger.info(
            'task %s: Codex chat id %s recorded', record.task_id, thread_id,
        )

    def _persist(self, record: AgentSessionRecord) -> None:
        try:
            write_record(self._state_dir, record, logger=self.logger)
        except Exception:
            self.logger.exception(
                'failed to persist the Codex chat record for task %s',
                record.task_id,
            )
        if callable(self._record_sink):
            try:
                self._record_sink(record)
            except Exception:
                self.logger.exception(
                    'failed to publish the Codex chat record for task %s',
                    record.task_id,
                )

    def mark_terminated(self, record: AgentSessionRecord) -> None:
        record.status = SESSION_STATUS_TERMINATED
        self._persist(record)
