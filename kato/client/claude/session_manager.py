"""Registry of live Claude planning sessions, one per Kato task.

Owns the lifecycle of :class:`StreamingClaudeSession` instances:

* Creates a session when the orchestrator (or webserver) declares a task is
  ready for planning.
* Persists session metadata (task id, claude session id, status, timestamps)
  to disk so a kato restart can rehydrate tabs in the planning UI.
* Tears sessions down when the ticket leaves a "live" state or when the
  process is shutting down.

Pure infrastructure — no Flask, no agent_service. The orchestrator and
the webserver both talk to this manager; the manager talks to the
streaming subprocess.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from kato.client.claude.streaming_session import StreamingClaudeSession
from kato.helpers.logging_utils import configure_logger
from kato.helpers.text_utils import normalized_text


SESSION_STATUS_ACTIVE = 'active'
SESSION_STATUS_DONE = 'done'
SESSION_STATUS_REVIEW = 'review'
SESSION_STATUS_TERMINATED = 'terminated'

SUPPORTED_SESSION_STATUSES = frozenset(
    {
        SESSION_STATUS_ACTIVE,
        SESSION_STATUS_DONE,
        SESSION_STATUS_REVIEW,
        SESSION_STATUS_TERMINATED,
    }
)


@dataclass
class PlanningSessionRecord(object):
    """On-disk metadata for one planning session.

    Stored as JSON at ``<state_dir>/<task_id>.json``. The live subprocess is
    NOT part of this record — only what's needed to rehydrate / display the
    tab after a restart. The actual conversation transcript lives inside
    Claude Code's own session storage and is rejoined via ``claude --resume``.
    """

    task_id: str
    task_summary: str = ''
    claude_session_id: str = ''
    status: str = SESSION_STATUS_ACTIVE
    created_at_epoch: float = field(default_factory=time.time)
    updated_at_epoch: float = field(default_factory=time.time)
    cwd: str = ''
    # The branch kato prepared for this task. The webserver compares this
    # against the repo's HEAD before forwarding any message to the live
    # subprocess; if they diverge (kato has moved on to a different task)
    # the send is rejected. Empty string disables the check (wait-planning
    # tabs that aren't owned by the orchestrator).
    expected_branch: str = ''

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> 'PlanningSessionRecord':
        return cls(
            task_id=str(payload.get('task_id', '') or ''),
            task_summary=str(payload.get('task_summary', '') or ''),
            claude_session_id=str(payload.get('claude_session_id', '') or ''),
            status=str(payload.get('status', SESSION_STATUS_ACTIVE) or SESSION_STATUS_ACTIVE),
            created_at_epoch=float(payload.get('created_at_epoch', time.time()) or time.time()),
            updated_at_epoch=float(payload.get('updated_at_epoch', time.time()) or time.time()),
            cwd=str(payload.get('cwd', '') or ''),
            expected_branch=str(payload.get('expected_branch', '') or ''),
        )


class ClaudeSessionManager(object):
    """Owns every active streaming Claude session for the running Kato.

    Thread-safe by design: the orchestrator may register / terminate sessions
    while the webserver simultaneously reads them.
    """

    DEFAULT_STATE_DIR_NAME = '.kato/sessions'

    @classmethod
    def from_config(
        cls,
        open_cfg,
        agent_backend: str,
    ) -> 'ClaudeSessionManager | None':
        """Build the manager (or return None) from the kato config block.

        Only the Claude backend exposes live in-process sessions for the UI
        to talk to; everything else returns None and the planning webserver
        gracefully shows an empty tab list.
        """
        if str(agent_backend or '').strip().lower() != 'claude':
            return None
        state_dir = (
            os.environ.get('KATO_SESSION_STATE_DIR', '').strip()
            or str(Path.home() / cls.DEFAULT_STATE_DIR_NAME)
        )
        return cls(state_dir=state_dir)

    def __init__(
        self,
        *,
        state_dir: str | os.PathLike[str],
        session_factory=None,
    ) -> None:
        self._state_dir = Path(state_dir)
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._session_factory = session_factory or StreamingClaudeSession
        self._lock = threading.RLock()
        self._sessions: dict[str, StreamingClaudeSession] = {}
        self._records: dict[str, PlanningSessionRecord] = {}
        self.logger = configure_logger(self.__class__.__name__)
        self._load_persisted_records()

    # ----- public API -----

    def start_session(
        self,
        *,
        task_id: str,
        task_summary: str = '',
        initial_prompt: str = '',
        binary: str = '',
        cwd: str = '',
        model: str = '',
        permission_mode: str = '',
        permission_prompt_tool: str = '',
        allowed_tools: str = '',
        disallowed_tools: str = '',
        max_turns: int | None = None,
        effort: str = '',
        env: dict[str, str] | None = None,
        expected_branch: str = '',
    ) -> StreamingClaudeSession:
        """Spawn (or rehydrate) the streaming session bound to ``task_id``.

        If a previous run wrote a record for this task, the new subprocess
        resumes the same Claude session id so the planning conversation
        picks up where it left off.
        """
        normalized_task_id = self._normalize_task_id(task_id)
        factory_kwargs = {
            'task_id': normalized_task_id,
            'binary': binary,
            'cwd': cwd,
            'model': model,
            'permission_mode': permission_mode,
            'permission_prompt_tool': permission_prompt_tool,
            'allowed_tools': allowed_tools,
            'disallowed_tools': disallowed_tools,
            'max_turns': max_turns,
            'effort': effort,
            'env': env,
        }
        with self._lock:
            existing = self._sessions.get(normalized_task_id)
            if existing is not None and existing.is_alive:
                return existing
            previous_record = self._records.get(normalized_task_id)
            resume_session_id = self._resume_id_for_spawn(
                normalized_task_id, previous_record, existing,
            )
            session = self._spawn_with_resume_self_heal(
                normalized_task_id=normalized_task_id,
                factory_kwargs=factory_kwargs,
                initial_prompt=initial_prompt,
                resume_session_id=resume_session_id,
            )
            self._sessions[normalized_task_id] = session
            self._record_session_metadata(
                normalized_task_id=normalized_task_id,
                session=session,
                previous_record=previous_record,
                task_summary=task_summary,
                expected_branch=expected_branch,
                resume_session_id=resume_session_id,
            )
            return session

    def _resume_id_for_spawn(
        self,
        normalized_task_id: str,
        previous_record: PlanningSessionRecord | None,
        existing_session,
    ) -> str:
        """Return the resume id to pass to the next spawn (or '' for fresh).

        Self-heal: if the previous spawn for this task already died because
        Claude rejected the persisted resume id (e.g., the conversation
        was never persisted to ``~/.claude``, or the user wiped that
        directory), blank the stale id so the new spawn starts a fresh
        conversation. Without this we'd respawn against the dead id every
        scan cycle.
        """
        resume_session_id = previous_record.claude_session_id if previous_record else ''
        if not resume_session_id or existing_session is None:
            return resume_session_id
        if not self._died_with_stale_resume_id(existing_session, resume_session_id):
            return resume_session_id
        self.logger.warning(
            'task %s: claude rejected resume id %s; starting a fresh session',
            normalized_task_id,
            resume_session_id,
        )
        if previous_record is not None:
            previous_record.claude_session_id = ''
            self._persist_record(previous_record)
        return ''

    def _spawn_with_resume_self_heal(
        self,
        *,
        normalized_task_id: str,
        factory_kwargs: dict,
        initial_prompt: str,
        resume_session_id: str,
    ) -> StreamingClaudeSession:
        """Spawn the subprocess, retrying once if a stale resume id rejects.

        Claude exits within ~1s when ``--resume`` references a missing
        session, so a short polling window catches the failure before the
        first user-visible turn. On a hit, we terminate, drop the resume
        id, and respawn fresh.
        """
        session = self._session_factory(
            resume_session_id=resume_session_id, **factory_kwargs,
        )
        session.start(initial_prompt=initial_prompt)
        if not resume_session_id:
            return session
        if not self._wait_for_stale_resume_failure(session, resume_session_id):
            return session
        self.logger.warning(
            'task %s: claude rejected resume id %s on first spawn; '
            'retrying with a fresh session',
            normalized_task_id,
            resume_session_id,
        )
        try:
            session.terminate()
        except Exception:
            pass
        session = self._session_factory(
            resume_session_id='', **factory_kwargs,
        )
        session.start(initial_prompt=initial_prompt)
        return session

    def _record_session_metadata(
        self,
        *,
        normalized_task_id: str,
        session: StreamingClaudeSession,
        previous_record: PlanningSessionRecord | None,
        task_summary: str,
        expected_branch: str,
        resume_session_id: str,
    ) -> None:
        """Build and persist the on-disk record for the just-spawned session."""
        record = PlanningSessionRecord(
            task_id=normalized_task_id,
            task_summary=normalized_text(task_summary)
            or (previous_record.task_summary if previous_record else ''),
            claude_session_id=session.claude_session_id or resume_session_id,
            status=SESSION_STATUS_ACTIVE,
            created_at_epoch=(
                previous_record.created_at_epoch
                if previous_record
                else time.time()
            ),
            updated_at_epoch=time.time(),
            cwd=session.cwd,
            # Always use the caller's value — wait-planning explicitly
            # passes '' (no lock), and the autonomous runner always passes
            # a real branch. Falling back to the persisted value would
            # silently re-arm a stale lock from a prior buggy run.
            expected_branch=normalized_text(expected_branch),
        )
        self._records[normalized_task_id] = record
        self._persist_record(record)

    def get_session(self, task_id: str) -> StreamingClaudeSession | None:
        with self._lock:
            return self._sessions.get(self._normalize_task_id(task_id))

    def get_record(self, task_id: str) -> PlanningSessionRecord | None:
        with self._lock:
            record = self._records.get(self._normalize_task_id(task_id))
            return self._with_refreshed_session_id(record)

    def list_records(self) -> list[PlanningSessionRecord]:
        with self._lock:
            return [
                self._with_refreshed_session_id(record)
                for record in self._records.values()
            ]

    def update_status(self, task_id: str, status: str) -> None:
        if status not in SUPPORTED_SESSION_STATUSES:
            raise ValueError(
                f'unknown session status: {status!r}; '
                f'supported: {sorted(SUPPORTED_SESSION_STATUSES)}'
            )
        normalized_task_id = self._normalize_task_id(task_id)
        with self._lock:
            record = self._records.get(normalized_task_id)
            if record is None:
                return
            record.status = status
            record.updated_at_epoch = time.time()
            self._persist_record(record)

    def terminate_session(self, task_id: str, *, remove_record: bool = False) -> None:
        normalized_task_id = self._normalize_task_id(task_id)
        with self._lock:
            session = self._sessions.pop(normalized_task_id, None)
            if session is not None:
                try:
                    session.terminate()
                except Exception:
                    self.logger.exception(
                        'failed to terminate streaming session for task %s',
                        normalized_task_id,
                    )
            if remove_record:
                self._records.pop(normalized_task_id, None)
                self._delete_persisted_record(normalized_task_id)
            else:
                record = self._records.get(normalized_task_id)
                if record is not None:
                    record.status = SESSION_STATUS_TERMINATED
                    record.updated_at_epoch = time.time()
                    self._persist_record(record)

    def shutdown(self) -> None:
        """Terminate every live session. Safe to call multiple times."""
        with self._lock:
            task_ids = list(self._sessions.keys())
        for task_id in task_ids:
            self.terminate_session(task_id)

    # ----- internals -----

    @classmethod
    def _wait_for_stale_resume_failure(
        cls,
        session,
        resume_session_id: str,
        *,
        max_wait_seconds: float = 4.0,
        poll_interval_seconds: float = 0.1,
    ) -> bool:
        """Poll briefly for Claude to reject the resume id and return True if it did.

        Claude exits within a second or two when ``--resume`` references
        a missing session, so a short wait here is enough to catch the
        common case without delaying healthy spawns. Returns False on
        timeout (let the orchestrator carry on and self-heal on the
        next scan if the failure shows up later).
        """
        deadline = time.monotonic() + max(0.0, float(max_wait_seconds))
        while time.monotonic() < deadline:
            if not session.is_alive:
                return cls._died_with_stale_resume_id(session, resume_session_id)
            if cls._died_with_stale_resume_id(session, resume_session_id):
                return True
            time.sleep(poll_interval_seconds)
        return False

    @staticmethod
    def _died_with_stale_resume_id(session, resume_session_id: str) -> bool:
        """Did ``session`` exit because Claude couldn't find the resume id?

        We detect this from the captured stderr (where the CLI prints
        ``No conversation found with session ID: ...``) and from the
        terminal result text. The check is conservative — false positives
        only cost us a fresh session, but a missed positive would loop
        forever.
        """
        marker = f'No conversation found with session ID: {resume_session_id}'
        try:
            stderr_lines = session.stderr_snapshot()
        except Exception:
            stderr_lines = []
        for line in stderr_lines:
            if marker in line:
                return True
        terminal = getattr(session, 'terminal_event', None)
        if terminal is None:
            return False
        raw = getattr(terminal, 'raw', {}) or {}
        if not bool(raw.get('is_error', False)):
            return False
        result_text = str(raw.get('result', '') or '')
        return marker in result_text

    @staticmethod
    def _normalize_task_id(task_id: str) -> str:
        normalized = str(task_id or '').strip()
        if not normalized:
            raise ValueError('task_id is required')
        return normalized

    def _record_path(self, task_id: str) -> Path:
        # task ids in YouTrack/Jira/etc. tend to be filename-safe (e.g.
        # PROJ-123). We still strip any path separators just in case.
        safe_name = task_id.replace('/', '_').replace(os.sep, '_')
        return self._state_dir / f'{safe_name}.json'

    def _persist_record(self, record: PlanningSessionRecord) -> None:
        path = self._record_path(record.task_id)
        tmp_path = path.with_suffix('.json.tmp')
        try:
            tmp_path.write_text(
                json.dumps(record.to_dict(), indent=2, sort_keys=True),
                encoding='utf-8',
            )
            tmp_path.replace(path)
        except OSError as exc:
            self.logger.warning(
                'failed to persist planning session record for task %s: %s',
                record.task_id,
                exc,
            )

    def _delete_persisted_record(self, task_id: str) -> None:
        path = self._record_path(task_id)
        try:
            path.unlink()
        except FileNotFoundError:
            return
        except OSError as exc:
            self.logger.warning(
                'failed to remove planning session record for task %s: %s',
                task_id,
                exc,
            )

    def _load_persisted_records(self) -> None:
        if not self._state_dir.exists():
            return
        for path in sorted(self._state_dir.glob('*.json')):
            try:
                payload = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, json.JSONDecodeError) as exc:
                self.logger.warning(
                    'skipping unreadable planning session record %s: %s',
                    path,
                    exc,
                )
                continue
            if not isinstance(payload, dict):
                continue
            record = PlanningSessionRecord.from_dict(payload)
            if not record.task_id:
                continue
            # On startup the live subprocess is gone; reflect that so the
            # UI doesn't claim a tab is "active" when there's no subprocess
            # behind it. The agent_service cleanup loop will sweep these
            # records on the next scan for tasks that no longer need them.
            if record.status == SESSION_STATUS_ACTIVE:
                record.status = SESSION_STATUS_TERMINATED
                record.updated_at_epoch = time.time()
            self._records[record.task_id] = record

    def _with_refreshed_session_id(
        self,
        record: PlanningSessionRecord | None,
    ) -> PlanningSessionRecord | None:
        if record is None:
            return None
        session = self._sessions.get(record.task_id)
        if session is None:
            return record
        live_id = session.claude_session_id
        if live_id and live_id != record.claude_session_id:
            record.claude_session_id = live_id
            record.updated_at_epoch = time.time()
            self._persist_record(record)
        return record
