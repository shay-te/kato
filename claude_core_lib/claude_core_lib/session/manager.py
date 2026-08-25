"""Registry of live Claude planning sessions, one per the orchestrator task.

Owns the lifecycle of :class:`StreamingClaudeSession` instances:

* Creates a session when the orchestrator (or webserver) declares a task is
  ready for planning.
* Persists session metadata (task id, claude session id, status, timestamps)
  to disk so a the orchestrator restart can rehydrate tabs in the planning UI.
* Tears sessions down when the ticket leaves a "live" state or when the
  process is shutting down.

Pure infrastructure — no Flask, no agent_service. The orchestrator and
the webserver both talk to this manager; the manager talks to the
streaming subprocess.
"""

from __future__ import annotations

from agent_core_lib.agent_core_lib.data.agent_backend import AgentBackend
import os
import threading
import time
from pathlib import Path

from agent_core_lib.agent_core_lib.helpers.logging_utils import configure_logger
from agent_core_lib.agent_core_lib.session.record_files import (
    delete_record,
    load_records,
    record_key,
    write_record,
)
from agent_core_lib.agent_core_lib.session.record import (
    SESSION_STATUS_ACTIVE,
    SESSION_STATUS_TERMINATED,
    SUPPORTED_SESSION_STATUSES,
    AgentSessionRecord,
    session_id_list,
)
from agent_core_lib.agent_core_lib.helpers.session_id_utils import (
    AGENT_SESSION_ID,
    fix_session_id,
    has_session_id,
    read_session_id_from,
    same_session_id,
)
from utils_core_lib.utils_core_lib.text_utils import normalized_text
from claude_core_lib.claude_core_lib.session.streaming import StreamingClaudeSession











class ClaudeSessionManager(object):
    """Owns every active streaming Claude session for the running the orchestrator.

    Thread-safe by design: the orchestrator may register / terminate sessions
    while the webserver simultaneously reads them.
    """

    # Generic standalone default. In production the host (orchestrator) passes
    # an explicit ``state_dir`` to ``from_config`` — it owns where session
    # metadata lives — so this per-user fallback only applies to standalone
    # use of the lib.
    DEFAULT_STATE_DIR_NAME = '.claude-agent/sessions'

    @classmethod
    def from_config(
        cls,
        open_cfg,
        agent_backend: str,
        *,
        state_dir: str = '',
    ) -> 'ClaudeSessionManager | None':
        """Build the manager (or return None) for the Claude backend.

        Only the Claude backend exposes live in-process sessions for the UI
        to talk to; everything else returns None and the planning webserver
        gracefully shows an empty tab list. ``state_dir`` is supplied by the
        caller (the host decides where session metadata lives); when omitted
        the lib falls back to a generic per-user default.
        """
        if not AgentBackend.is_a(agent_backend, AgentBackend.CLAUDE):
            return None
        resolved = str(state_dir or '').strip() or str(
            Path.home() / cls.DEFAULT_STATE_DIR_NAME
        )
        return cls(state_dir=resolved)

    #: The backend this manager spawns. Stamped onto every record it writes
    #: so a chat remembers which CLI produced it even after the operator
    #: switches backends.
    AGENT_BACKEND = AgentBackend.CLAUDE.value

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
        # Per-task spawn locks. Held during the (slow) Claude subprocess
        # spawn so two concurrent spawns for the SAME task serialize, while
        # spawns for DIFFERENT tasks run in parallel. The global ``_lock``
        # protects the registry mutations only — never held across the
        # spawn itself, which would serialize all parallel-runner workers.
        self._spawn_locks: dict[str, threading.Lock] = {}
        self._sessions: dict[str, StreamingClaudeSession] = {}
        self._records: dict[str, AgentSessionRecord] = {}
        self._workspace_manager = None
        # Default ``done_callback`` + done-sentinel injected into every
        # spawned session. The host sets both via ``set_done_callback`` so the
        # agent printing the host's sentinel triggers the publish flow. The
        # sentinel string stays host-supplied so this lib is product-agnostic.
        self._done_callback = None
        self._done_sentinel = ''
        self.logger = configure_logger(self.__class__.__name__)
        self._records.update(load_records(self._state_dir, logger=self.logger))

    def set_done_callback(self, callback, done_sentinel: str = '') -> None:
        """Register the done-callback + the sentinel that triggers it.

        Called once during host startup wiring with the host's
        finish-session callback and the host's done-sentinel token (the
        agent prints that token to end the chat). Every session spawned
        after this picks up both automatically. The sentinel is supplied by
        the caller so the lib carries no product-specific token.
        """
        self._done_callback = callback
        self._done_sentinel = str(done_sentinel or '')

    def attach_workspace_manager(self, workspace_manager) -> None:
        """Mirror agent_session_id + cwd into workspace metadata as we capture them.

        Optional wiring: when the orchestrator boots both managers it calls
        this so the orchestrator has a single source of truth for "which Claude session
        belongs to this task" living next to the workspace folder.
        """
        self._workspace_manager = workspace_manager
        self._seed_records_from_workspaces()

    def _seed_records_from_workspaces(self) -> None:
        """Recover Claude session ids from workspace metadata on boot.

        If the orchestrator's own state dir was wiped (or is on a different host than
        the previous run), the per-task AgentSessionRecord is missing
        but the workspace folder still has ``.the orchestrator-meta.json`` with the
        Claude session id. Fold those into the in-memory records so the
        next spawn can ``--resume`` cleanly.
        """
        if self._workspace_manager is None:
            return
        try:
            workspace_records = self._workspace_manager.list_workspaces()
        except Exception:
            self.logger.exception('failed to list workspaces during session seed')
            return
        with self._lock:
            for workspace in workspace_records:
                # ``agent_session_id`` is workspace_core_lib's generic name
                # for the bound agent session id.
                agent_session_id = read_session_id_from(workspace)
                if not agent_session_id:
                    continue
                lookup_key = self._lookup_key(workspace.task_id)
                existing = self._records.get(lookup_key)
                existing_id = read_session_id_from(existing)
                if existing is not None and existing_id:
                    continue
                record = existing or AgentSessionRecord(
                    agent_backend=self.AGENT_BACKEND,
                    task_id=workspace.task_id,
                    task_summary=str(getattr(workspace, 'task_summary', '') or ''),
                    status=SESSION_STATUS_TERMINATED,
                )
                record.agent_session_id = agent_session_id
                cwd = str(getattr(workspace, 'cwd', '') or '').strip()
                if cwd and not record.cwd:
                    record.cwd = cwd
                record.updated_at_epoch = time.time()
                self._records[lookup_key] = record
                self._persist_record(record)

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
        architecture_doc_path: str = '',
        lessons_path: str = '',
        docker_mode_on: bool = False,
        sandbox_root: str = '',
        additional_dirs: list[str] | None = None,
    ) -> StreamingClaudeSession:
        """Spawn (or rehydrate) the streaming session bound to ``task_id``.

        If a previous run wrote a record for this task, the new subprocess
        resumes the same Claude session id so the planning conversation
        picks up where it left off.
        """
        normalized_task_id = self._normalize_task_id(task_id)
        lookup_key = self._lookup_key(task_id)
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
            'architecture_doc_path': architecture_doc_path,
            'lessons_path': lessons_path,
            'docker_mode_on': docker_mode_on,
            # Task folder for the docker bind mount; '' keeps the old
            # cwd-only mount. See StreamingClaudeSession._sandbox_mount.
            'sandbox_root': sandbox_root,
            'additional_dirs': list(additional_dirs or []),
            'done_callback': self._done_callback,
            'done_sentinel': self._done_sentinel,
        }
        # Per-task spawn lock: get-or-create under the global lock, then
        # hold the per-task lock (NOT the global lock) for the actual
        # spawn. This is what lets parallel-runner workers spawn
        # different-task sessions concurrently — earlier the global lock
        # was held across the spawn and serialised everything.
        with self._lock:
            spawn_lock = self._spawn_locks.setdefault(
                lookup_key, threading.Lock(),
            )
        with spawn_lock:
            with self._lock:
                existing = self._sessions.get(lookup_key)
                if existing is not None and existing.is_alive:
                    drifted = self._discard_if_session_id_drifted_locked(
                        lookup_key, normalized_task_id, existing,
                    )
                    if not drifted:
                        return existing
                    existing = None
                previous_record = self._records.get(lookup_key)
                resume_session_id = self._resume_id_for_spawn(
                    normalized_task_id,
                    previous_record,
                    existing,
                )
            # Warn on huge transcripts, but never trade away the user's
            # session id. A slow resume is better than silent session drift.
            resume_session_id = self._gate_resume_by_jsonl_size(
                normalized_task_id, resume_session_id,
            )
            # One-session-per-task invariant: if the task already has a
            # session id on file, ensure the JSONL transcript is present
            # at the spawn cwd's project dir before passing ``--resume``.
            # ``claude --resume <id>`` is cwd-keyed — it looks at
            # ``~/.claude/projects/<encoded-cwd>/<id>.jsonl`` only — so
            # when the orchestrator switches cwds across spawns (workspace clone vs
            # source repo, sibling repos in a multi-repo task) the
            # resume previously failed with "No conversation found" and
            # stale-resume handling blanked the id, ending the conversation. The
            # JSONL itself is a plain file: we copy it to the new cwd's
            # project dir and Claude resumes natively. Free in tokens
            # and idempotent.
            self._ensure_resume_jsonl_at_target_cwd(
                resume_session_id=resume_session_id,
                target_cwd=cwd,
            )
            # A session still HELD by a live CLI process cannot be
            # resumed — ``claude --resume`` silently starts a blank,
            # memoryless conversation under a new id. On Windows the caller
            # itself used to create such holders: ``claude`` resolves
            # to the npm ``claude.cmd`` shim, so kills hit the cmd.exe
            # wrapper and orphaned the real CLI (fixed in
            # ``StreamingClaudeSession`` with a tree kill, but leftovers
            # from crashes / closed consoles still happen). Wait for /
            # kill any leftover holder before passing ``--resume``.
            self._terminate_stale_resume_holders(
                normalized_task_id, resume_session_id,
            )
            # Spawn happens with NO global lock held — concurrent spawns
            # for different task ids run in parallel.
            session = self._spawn_with_resume_self_heal(
                normalized_task_id=normalized_task_id,
                factory_kwargs=factory_kwargs,
                initial_prompt=initial_prompt,
                resume_session_id=resume_session_id,
            )
            # Capture a first-spawn id, but never let a live process
            # replace an already-pinned operator session id.
            correction_expected_id = fix_session_id(session.agent_session_id)
            correction_can_replace = not bool(resume_session_id)
            session._session_id_correction_callback = (
                lambda sid, k=lookup_key, t=normalized_task_id,
                expected=correction_expected_id,
                can_replace=correction_can_replace,
                source=session: (
                    self._correct_session_id_in_record(
                        k, t, sid,
                        expected_existing_id=expected,
                        can_replace_existing=can_replace,
                        source_session=source,
                    )
                )
            )
            with self._lock:
                self._sessions[lookup_key] = session
                self._record_session_metadata(
                    normalized_task_id=normalized_task_id,
                    session=session,
                    previous_record=previous_record,
                    task_summary=task_summary,
                    expected_branch=expected_branch,
                    resume_session_id=resume_session_id,
                )
            return session

    def _ensure_resume_jsonl_at_target_cwd(
        self,
        *,
        resume_session_id: str,
        target_cwd: str,
    ) -> None:
        """Copy the resume JSONL into ``target_cwd``'s project dir if needed.

        Defends the one-session-per-task invariant against cwd drift.
        ``claude --resume`` only finds a transcript under the spawn
        cwd's encoded project dir; the orchestrator's cwd legitimately changes
        across operations on the same task (review-fix in a sibling
        repo, retargeted workspace clone, etc.), so we copy the JSONL
        to wherever Claude will look for it. No-op when there's no
        resume id, no target cwd, no source transcript on disk, or
        the source already lives at the target.
        """
        if not resume_session_id or not target_cwd:
            return
        try:
            from claude_core_lib.claude_core_lib.session.history import (
                find_session_file,
            )
            from claude_core_lib.claude_core_lib.session.index import (
                claude_project_dir_for_cwd,
                migrate_session_to_workspace,
            )
        except ImportError:
            return
        try:
            source = find_session_file(resume_session_id)
        except Exception:
            self.logger.exception(
                'failed to locate resume transcript for session %s',
                resume_session_id,
            )
            return
        if source is None:
            # We have a resume id but no transcript on disk anywhere under
            # ~/.claude/projects. ``claude --resume <id>`` will still be
            # passed, but with nothing to load Claude spawns a memoryless
            # session that LOOKS resumed ("session started · <id>" with no
            # prior context) — exactly the "he forgot everything after
            # restart" symptom. Nothing we can copy here; warn loudly so the
            # operator sees WHY continuity was lost instead of a silent drop.
            self.logger.warning(
                'resume id %s has no transcript on disk (looked under '
                '~/.claude/projects); spawn at %s will start without prior '
                'conversation history',
                resume_session_id,
                target_cwd,
            )
            return
        try:
            target_dir = claude_project_dir_for_cwd(target_cwd)
            if source.parent.resolve() == target_dir.resolve():
                return
        except OSError:
            pass
        # The source JSONL is left in place as a historical snapshot
        # of the conversation at the moment the cwd switched. the orchestrator's
        # "one session per task" invariant lives at the session-id
        # level — the orchestrator's record points at exactly one id, and Claude
        # writes new turns only to the canonical copy at the spawn
        # cwd's project dir. The old file is harmless and useful for
        # forensics; orphan cleanup, if ever wanted, is a separate
        # housekeeping concern.
        try:
            copied = migrate_session_to_workspace(
                transcript_path=str(source),
                target_cwd=target_cwd,
            )
        except Exception:
            self.logger.exception(
                'failed to copy resume transcript for session %s into %s '
                '(--resume will likely fail; fresh-session drift is refused)',
                resume_session_id,
                target_cwd,
            )
            return
        if copied is None:
            # Copy quietly failed (best-effort path inside
            # migrate_session_to_workspace). Log at warning so the
            # next failed --resume is traceable.
            self.logger.warning(
                'task transcript migration returned None; resume id %s '
                'expected at %s — Claude will likely reject --resume '
                'and the orchestrator will refuse a fresh fallback',
                resume_session_id,
                target_dir,
            )
            return
        # Verify the file landed where Claude will look for it.
        # Without this guard, an unexpected filesystem outcome (race,
        # symlink, permission anomaly) would still fall through to
        # the spawn and waste 4-5s before refusing the fresh fallback.
        if not Path(copied).is_file():
            self.logger.warning(
                'migrated JSONL not present at %s after copy; --resume '
                'will reject and the orchestrator will refuse a fresh fallback',
                copied,
            )

    def _terminate_stale_resume_holders(
        self,
        task_id: str,
        resume_session_id: str,
    ) -> None:
        """Wait out / kill live CLI processes still holding the resume session.

        Claude Code registers every running CLI in
        ``~/.claude/sessions/<pid>.json``; resuming a session a live
        process still holds makes ``--resume`` silently start a fresh
        blank conversation (the "Claude forgot everything" bug).
        Best-effort: any failure here degrades to the spawn-side
        ``resume_was_ignored`` guard, it must never block the spawn.
        """
        if not resume_session_id:
            return
        try:
            from claude_core_lib.claude_core_lib.session.registry import (
                release_session_holders,
            )
        except ImportError:
            return
        try:
            released = release_session_holders(
                resume_session_id, logger=self.logger,
            )
        except Exception:
            self.logger.exception(
                'task %s: failed to check for live holders of session %s',
                task_id, resume_session_id,
            )
            return
        if not released:
            self.logger.warning(
                'task %s: session %s is STILL held by a live claude '
                'process; --resume will likely start a blank session '
                '(the caller will detect and refuse the memoryless impostor)',
                task_id, resume_session_id,
            )

    # Sessions whose JSONL transcript exceeds this byte count are NOT
    # resumed — the full history would exceed (or strain) the model's
    # context window, causing 10–15 minute startup delays before the
    # first token appears.  1 MB of JSONL ≈ 50–100 K tokens of real
    # content; well within Claude Opus's 200 K limit and loads in
    # under 30 s.  Above 1 MB the latency climbs sharply.
    _RESUME_JSONL_SIZE_LIMIT_BYTES: int = 1_000_000  # 1 MB

    def _gate_resume_by_jsonl_size(
        self,
        normalized_task_id: str,
        resume_session_id: str,
    ) -> str:
        """Warn when a transcript is huge, but always keep the resume id."""
        if not resume_session_id:
            return resume_session_id
        try:
            from claude_core_lib.claude_core_lib.session.history import find_session_file
            path = find_session_file(resume_session_id)
        except Exception:
            return resume_session_id
        if path is None:
            return resume_session_id
        try:
            size = path.stat().st_size
        except OSError:
            return resume_session_id
        if size <= self._RESUME_JSONL_SIZE_LIMIT_BYTES:
            return resume_session_id
        self.logger.warning(
            'task %s: session JSONL is %.0f KB (limit %d KB); '
            'resume may be slow, but keeping --resume to preserve the '
            'operator session id',
            normalized_task_id,
            size / 1024,
            self._RESUME_JSONL_SIZE_LIMIT_BYTES // 1024,
        )
        return resume_session_id

    def _resume_id_for_spawn(
        self,
        normalized_task_id: str,
        previous_record: AgentSessionRecord | None,
        existing_session,
    ) -> str:
        """Return the resume id to pass to the next spawn (or '' for fresh).

        Even when a previous live process rejected the id, keep returning
        it. the orchestrator must fail loud rather than silently drift to a fresh
        Claude session id.
        """
        raw_resume_id = previous_record.agent_session_id if previous_record else ''
        resume_session_id = fix_session_id(raw_resume_id)
        if previous_record is not None and raw_resume_id != resume_session_id:
            previous_record.agent_session_id = resume_session_id
            self._persist_record(previous_record)
        if not resume_session_id or existing_session is None:
            return resume_session_id
        # Poisoned transcript (model switch mid-chat / a failed prior turn
        # left an invalid ``previous_message_id``) — the API rejects every
        # resume of this id with a 400. This corruption is PERMANENT, so
        # unlike a stale id we don't keep it pinned and retry; we abandon
        # it and start fresh so the chat recovers. Returning '' routes
        # through the normal fresh-spawn path, which re-pins the new id.
        if self._died_with_poisoned_resume(existing_session):
            self.logger.warning(
                'task %s: the resumed conversation for session id %s can no '
                'longer be continued (model switch mid-chat or a failed '
                'prior turn left an invalid previous_message_id; the API '
                'rejects every resume). Abandoning it and starting a fresh '
                'session — prior chat context is not carried over.',
                normalized_task_id,
                resume_session_id,
            )
            return ''
        if not self._died_with_stale_resume_id(existing_session, resume_session_id):
            return resume_session_id
        self.logger.warning(
            'task %s: claude rejected resume id %s; keeping it pinned '
            'and retrying because session id preservation is required',
            normalized_task_id,
            resume_session_id,
        )
        return resume_session_id

    def _spawn_with_resume_self_heal(
        self,
        *,
        normalized_task_id: str,
        factory_kwargs: dict,
        initial_prompt: str,
        resume_session_id: str,
    ) -> StreamingClaudeSession:
        """Spawn the subprocess, refusing to drift when a resume id rejects."""
        # Diagnostic: log where Claude will look for the JSONL so a
        # future "resume silently spawned fresh" report has the path
        # information without needing to attach a debugger.
        if resume_session_id:
            self._log_resume_jsonl_state(
                normalized_task_id=normalized_task_id,
                resume_session_id=resume_session_id,
                target_cwd=factory_kwargs.get('cwd', ''),
            )
        session = self._session_factory(
            resume_session_id=resume_session_id, **factory_kwargs,
        )
        session.start(initial_prompt=initial_prompt)
        if not resume_session_id:
            return session
        if not self._wait_for_stale_resume_failure(session, resume_session_id):
            if getattr(session, 'resume_was_ignored', False):
                return self._refuse_ignored_resume(
                    normalized_task_id, session, resume_session_id,
                )
            return session
        self.logger.warning(
            'task %s: claude rejected resume id %s on first spawn; '
            'refusing to start a fresh session because session id '
            'preservation is required',
            normalized_task_id,
            resume_session_id,
        )
        try:
            session.terminate()
        except Exception:
            pass
        raise RuntimeError(
            f'Claude rejected resume id {resume_session_id} for task '
            f'{normalized_task_id}; refusing to start a fresh session.'
        )

    def _refuse_ignored_resume(
        self,
        normalized_task_id: str,
        session: StreamingClaudeSession,
        resume_session_id: str,
    ):
        """Terminate a spawn whose ``--resume`` was silently ignored, then raise.

        The CLI announced a session id DIFFERENT from the requested
        resume id — it started a fresh, memoryless conversation that
        only LOOKS resumed. Letting it live is exactly the "Claude
        forgot what he was doing" bug: the user chats with a blank
        impostor while the caller's record still pins the real id. Kill it
        and fail loud; the pinned id stays intact so the next spawn
        (with the leftover holder now gone) resumes the real history.
        """
        self.logger.warning(
            'task %s: claude IGNORED --resume %s and started a fresh '
            'session with no conversation history; terminating the '
            'memoryless session and keeping the pinned id (a previous '
            'claude process was likely still holding the transcript — '
            'retry in a few seconds)',
            normalized_task_id,
            resume_session_id,
        )
        try:
            session.terminate()
        except Exception:
            pass
        raise RuntimeError(
            f'Claude ignored resume id {resume_session_id} for task '
            f'{normalized_task_id} and started a memoryless session; '
            f'refusing it. Retry in a few seconds — the caller keeps the '
            f'original session id pinned.'
        )

    def _log_resume_jsonl_state(
        self,
        *,
        normalized_task_id: str,
        resume_session_id: str,
        target_cwd: str,
    ) -> None:
        """Emit pre-spawn diagnostics for ``--resume`` so future failures are debuggable.

        Reports (1) where the JSONL transcript actually lives on
        disk (via the cwd-agnostic glob lookup) and (2) where the
        spawn's cwd would make Claude look for it. A mismatch means
        ``--resume`` will fail unless
        ``_ensure_resume_jsonl_at_target_cwd`` copies the JSONL.
        """
        try:
            from claude_core_lib.claude_core_lib.session.history import (
                find_session_file,
            )
            from claude_core_lib.claude_core_lib.session.index import (
                claude_project_dir_for_cwd,
            )
        except ImportError:
            return
        try:
            source = find_session_file(resume_session_id)
        except Exception:
            self.logger.exception(
                'task %s: failed to locate JSONL for resume id %s',
                normalized_task_id, resume_session_id,
            )
            return
        try:
            target_dir = claude_project_dir_for_cwd(target_cwd) if target_cwd else None
        except Exception:
            target_dir = None
        source_dir = str(source.parent) if source is not None else '(not found)'
        target_text = str(target_dir) if target_dir is not None else '(no cwd)'
        matches = (
            target_dir is not None
            and source is not None
            and source.parent.resolve() == target_dir.resolve()
        )
        self.logger.info(
            'task %s: --resume %s; JSONL at %s; spawn cwd dir %s; aligned=%s',
            normalized_task_id,
            resume_session_id,
            source_dir,
            target_text,
            matches,
        )

    def _correct_session_id_in_record(
        self, lookup_key: str, task_id: str, actual_id: str,
        *,
        expected_existing_id: str = '',
        can_replace_existing: bool = False,
        source_session=None,
    ) -> None:
        """Update the persisted record when Claude reports a different session id.

        Called from the session's ``_session_id_correction_callback`` (fired
        from the session reader thread when the init event arrives).  Thread-safe
        via ``_lock``.  Updates both the in-memory record and its on-disk
        counterpart so the next ``start_session`` for this task resumes from
        Claude's actual JSONL rather than the orchestrator's expected UUID.
        """
        actual_id = fix_session_id(actual_id)
        if not has_session_id(actual_id):
            return
        with self._lock:
            record = self._records.get(lookup_key)
            if record is None:
                return
            record_id = fix_session_id(record.agent_session_id)
            if same_session_id(record_id, actual_id):
                if record.agent_session_id != actual_id:
                    record.agent_session_id = actual_id
                    record.updated_at_epoch = time.time()
                    self._persist_record(record)
                return
            if not record_id and source_session is not None \
                    and self._sessions.get(lookup_key) is not source_session:
                # A BLANK record id is an intentional state ("fresh chat"
                # detached the conversation) — only the CURRENTLY registered
                # session may fill it. The reader thread of a just-terminated
                # subprocess can outlive terminate (join timeout) and fire a
                # late init; accepting its id here would silently re-pin the
                # detached chat as active and undo the operator's new chat.
                self.logger.info(
                    'task %s: ignoring session id %s reported by a '
                    'no-longer-registered session (record is intentionally '
                    'detached)',
                    task_id, actual_id,
                )
                return
            if record_id:
                can_replace = (
                    can_replace_existing
                    and same_session_id(expected_existing_id, record_id)
                )
                if not can_replace:
                    self.logger.warning(
                        'task %s: live Claude reported session id %s, but '
                        'record is pinned to %s; keeping the persisted id',
                        task_id, actual_id, record_id,
                    )
                    return
                self.logger.warning(
                    'task %s: fresh spawn reported actual session id %s '
                    'instead of requested %s; recording actual id',
                    task_id, actual_id, record_id,
                )
            else:
                self.logger.info(
                    'task %s: recording live agent_session_id %s',
                    task_id, actual_id,
                )
            record.agent_session_id = actual_id
            record.updated_at_epoch = time.time()
            self._persist_record(record)

    def _record_session_metadata(
        self,
        *,
        normalized_task_id: str,
        session: StreamingClaudeSession,
        previous_record: AgentSessionRecord | None,
        task_summary: str,
        expected_branch: str,
        resume_session_id: str,
    ) -> None:
        """Build and persist the on-disk record for the just-spawned session."""
        active_id = (
            fix_session_id(resume_session_id)
            or read_session_id_from(session)
        )
        record = AgentSessionRecord(
            agent_backend=self.AGENT_BACKEND,
            task_id=normalized_task_id,
            task_summary=normalized_text(task_summary)
            or (previous_record.task_summary if previous_record else ''),
            agent_session_id=active_id,
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
            # Chat history survives respawns — only start_new_chat edits it.
            previous_session_ids=list(
                previous_record.previous_session_ids if previous_record else [],
            ),
        )
        self._records[self._lookup_key(normalized_task_id)] = record
        self._persist_record(record)

    def get_session(self, task_id: str) -> StreamingClaudeSession | None:
        """The live session for ``task_id``, or ``None``.

        The dictionary lookup takes the global lock; the LIVENESS PROBE does
        not. Probing under the global lock is what let one unresponsive CLI
        freeze every caller of this manager — including the UI's git actions,
        which only ask so they can show a "restart the tab" hint. A slow agent
        must never be able to hold a lock that git work waits on.
        """
        lookup_key = self._lookup_key(task_id)
        with self._lock:
            session = self._sessions.get(lookup_key)
        if session is None:
            return None
        if getattr(session, 'is_alive', False):
            with self._lock:
                drifted = self._discard_if_session_id_drifted_locked(
                    lookup_key, self._normalize_task_id(task_id), session,
                )
            if drifted:
                return None
        return session

    def save_record(self, record: AgentSessionRecord) -> None:
        """Persist an already-held record back to disk.

        For fields a CALLER owns and updates between turns — e.g. the last
        context-window reading, which is measured from a live subprocess but
        has to outlive it so the UI can still show it once the session sleeps.
        """
        if record is None:
            return
        self._persist_record(record)

    def get_record(self, task_id: str) -> AgentSessionRecord | None:
        with self._lock:
            record = self._records.get(self._lookup_key(task_id))
            return self._with_refreshed_session_id(record)

    def list_records(self) -> list[AgentSessionRecord]:
        with self._lock:
            return [
                self._with_refreshed_session_id(record)
                for record in self._records.values()
            ]

    def adopt_session_id(
        self,
        task_id: str,
        *,
        agent_session_id: str,
        task_summary: str = '',
    ) -> AgentSessionRecord:
        """Bind ``agent_session_id`` to ``task_id`` so the next spawn resumes it.

        Used by the planning UI when an operator picks an existing
        Claude Code session (e.g. a VS Code extension chat) to hand
        off to the orchestrator. The next ``start_session`` for ``task_id`` will
        ``--resume <agent_session_id>`` instead of starting a fresh
        conversation.

        Adoption does NOT change the spawn cwd — the orchestrator continues to
        run Claude at its per-task workspace clone, with a SNAPSHOT
        copy of the source JSONL placed under that clone's projects
        dir. This keeps the orchestrator edits isolated from the operator's live
        VS Code checkout (a hard-won property: the operator wants
        the orchestrator's git state separate from their working copy). The
        snapshot does mean the resumed conversation diverges from
        the source instance the moment either side takes another
        turn — see ``docs/adopting-existing-claude-sessions.md`` for
        the full lifecycle.

        The adopted id is mirrored to the workspace metadata so it
        survives a the orchestrator restart, and persisted to the per-task record
        so an in-process reader sees it immediately. If a live session
        is already running for ``task_id`` the caller is expected to
        terminate it first — adoption doesn't tear down a running
        subprocess on its own (that would be a confusing implicit
        side-effect).
        """
        new_id = fix_session_id(agent_session_id)
        if not new_id:
            raise ValueError('agent_session_id must be non-empty')
        normalized_task_id = self._normalize_task_id(task_id)
        lookup_key = self._lookup_key(task_id)
        with self._lock:
            spawn_lock = self._spawn_locks.setdefault(
                lookup_key, threading.Lock(),
            )
        with spawn_lock:
            now = time.time()
            with self._lock:
                # Share the start_session lock so adoption cannot slip
                # between spawn and metadata persistence for this task.
                existing_session = self._sessions.get(lookup_key)
                if existing_session is not None and getattr(
                    existing_session, 'is_alive', False,
                ):
                    raise RuntimeError(
                        f'cannot adopt session id for task {normalized_task_id}: '
                        f'a live Claude subprocess is still running for this '
                        f'task. Terminate the live session first '
                        f'(``terminate_session(task_id)``) before adopting; '
                        f'otherwise the next message would silently reuse the '
                        f'running subprocess instead of resuming the adopted id.'
                    )
                record = self._records.get(lookup_key)
                if record is None:
                    record = AgentSessionRecord(
                        agent_backend=self.AGENT_BACKEND,
                        task_id=normalized_task_id,
                        task_summary=str(task_summary or ''),
                        status=SESSION_STATUS_TERMINATED,
                    )
                    self._records[lookup_key] = record
                existing_id = fix_session_id(record.agent_session_id)
                if existing_id and existing_id != new_id:
                    raise RuntimeError(
                        f'cannot adopt session id {new_id} for task '
                        f'{normalized_task_id}: existing session id '
                        f'{existing_id} is already pinned'
                    )
                record.agent_session_id = new_id
                if task_summary and not record.task_summary:
                    record.task_summary = str(task_summary)
                record.updated_at_epoch = now
                self._persist_record(record)
                self._mirror_to_workspace_metadata(record)
                return record

    def start_new_chat(
        self,
        task_id: str,
        *,
        agent_session_id: str = '',
    ) -> AgentSessionRecord:
        """Detach the task's current chat; optionally re-attach a previous one.

        The detached chat's session id is pushed onto
        ``record.previous_session_ids`` so the operator can navigate back to
        it later. With an empty ``agent_session_id`` the next message spawns
        a brand-new Claude session ("fresh chat"); with a non-empty id
        (typically one of ``previous_session_ids``) the next spawn resumes
        that conversation instead. A live subprocess is terminated first —
        unlike ``adopt_session_id`` (an external handoff that refuses to
        tear down a running process), switching chats is the operator
        explicitly leaving the current one.

        On a fresh chat the workspace-metadata mirror is also blanked:
        ``resolve_agent_session_id`` falls back to it when the record's id
        is empty, so a stale mirror would replay the OLD chat's transcript
        into the new tab (and re-pin the old id on the next boot's
        workspace seed).
        """
        target_id = fix_session_id(agent_session_id)
        normalized_task_id = self._normalize_task_id(task_id)
        lookup_key = self._lookup_key(task_id)
        with self._lock:
            spawn_lock = self._spawn_locks.setdefault(
                lookup_key, threading.Lock(),
            )
        with spawn_lock:
            with self._lock:
                record = self._records.get(lookup_key)
                if record is None:
                    raise ValueError(
                        f'no session record for task {normalized_task_id}'
                    )
                if target_id and target_id == fix_session_id(record.agent_session_id):
                    return record  # already the active chat — nothing to do
            # Kill the live subprocess (if any). terminate_session also
            # flips the record status to TERMINATED and persists it.
            self.terminate_session(task_id)
            with self._lock:
                # Re-fetch with a guard: terminate_session drops the global
                # lock between our two locked sections, and a concurrent
                # forget-task (terminate_session(remove_record=True) takes
                # only the global lock, not this spawn lock) can pop the
                # record in that window.
                record = self._records.get(lookup_key)
                if record is None:
                    raise ValueError(
                        f'no session record for task {normalized_task_id}'
                    )
                current_id = fix_session_id(record.agent_session_id)
                history = [
                    sid for sid in session_id_list(record.previous_session_ids)
                    if sid != target_id
                ]
                if current_id and current_id != target_id and current_id not in history:
                    history.append(current_id)
                record.previous_session_ids = history
                record.agent_session_id = target_id
                record.updated_at_epoch = time.time()
                # The old chat's readings describe a conversation the
                # operator just left. Keeping them would have the cost
                # indicator report the NEW chat as instantly expensive.
                record.context_used_tokens = 0
                record.context_baseline_tokens = 0
                # Clear the workspace mirror BEFORE persisting: if the orchestrator dies
                # between the two steps, the record still holds the old id
                # (chat unchanged, mirror re-syncs on the next persist) —
                # whereas persist-then-clear could leave a blank record with
                # a stale mirror that resurrects the old chat via the
                # workspace-seed/history-replay fallbacks.
                if not target_id:
                    self._clear_workspace_agent_session(record.task_id)
                self._persist_record(record)
                return record

    def _clear_workspace_agent_session(self, task_id: str) -> None:
        if self._workspace_manager is None:
            return
        try:
            self._workspace_manager.clear_agent_session(task_id)
        except Exception:
            self.logger.exception(
                'failed to clear workspace agent session id for task %s',
                task_id,
            )

    def update_status(self, task_id: str, status: str) -> None:
        if status not in SUPPORTED_SESSION_STATUSES:
            raise ValueError(
                f'unknown session status: {status!r}; '
                f'supported: {sorted(SUPPORTED_SESSION_STATUSES)}'
            )
        normalized_task_id = self._normalize_task_id(task_id)
        lookup_key = self._lookup_key(task_id)
        with self._lock:
            record = self._records.get(lookup_key)
            if record is None:
                return
            record.status = status
            record.updated_at_epoch = time.time()
            self._persist_record(record)

    def terminate_session(self, task_id: str, *, remove_record: bool = False) -> None:
        normalized_task_id = self._normalize_task_id(task_id)
        lookup_key = self._lookup_key(task_id)
        with self._lock:
            session = self._sessions.pop(lookup_key, None)
            if session is not None:
                try:
                    session.terminate()
                except Exception:
                    self.logger.exception(
                        'failed to terminate streaming session for task %s',
                        normalized_task_id,
                    )
            if remove_record:
                # Capture the record BEFORE dropping it — we need its
                # Claude session id to delete the CLI transcript.
                removed = self._records.pop(lookup_key, None)
                delete_record(
                    self._state_dir, normalized_task_id, logger=self.logger,
                )
                self._forget_claude_transcript(removed, normalized_task_id)
            else:
                record = self._records.get(lookup_key)
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
        timeout and lets the normal session path continue.
        """
        deadline = time.monotonic() + max(0.0, float(max_wait_seconds))
        while time.monotonic() < deadline:
            # Resume verdict already known from the init event — stop
            # polling. A confirmed resume is healthy; an IGNORED resume
            # is not "stale-id death" (the subprocess is alive) but the
            # caller checks ``resume_was_ignored`` right after this
            # returns and refuses the memoryless session.
            if getattr(session, 'resume_confirmed', False):
                return False
            if getattr(session, 'resume_was_ignored', False):
                return False
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
        terminal result text. The check is conservative because a false
        positive blocks a spawn that might have been healthy.

        The function REQUIRES the subprocess to have actually exited.
        An alive subprocess whose stderr happens to contain the marker
        text (e.g., a log line from Claude or a tool that echoes the
        session id for diagnostics) MUST NOT trigger stale-resume handling.
        """
        # Only an exited subprocess can be "died with stale resume id".
        # A still-alive session that happens to surface the marker in
        # stderr (e.g., a tool output) is NOT the failure mode we're
        # detecting.
        if bool(getattr(session, 'is_alive', False)):
            return False
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
    def _died_with_poisoned_resume(session) -> bool:
        """Did ``session`` exit because its resumed transcript can't be continued?

        The Anthropic API rejects the next request of a resumed conversation
        whose stored continuation is broken — the operator switched models
        mid-chat, or a prior turn failed before a valid assistant message was
        written, so the CLI sends an invalid ``previous_message_id`` and the
        API returns ``400 ... previous_message_id: must be the id from a prior
        /v1/messages response``. Unlike a stale id (a live holder still owns
        the transcript — transient, so the caller keeps it pinned and retries) this
        corruption is PERMANENT: every resume re-hits the same 400. The only
        recovery is a fresh session, so the caller heals instead of refusing.

        Conservative — REQUIRES the subprocess to have EXITED (an alive
        session that merely echoes the marker in a tool output can't trip
        it) AND the distinctive ``previous_message_id`` token to appear in
        the terminal error result or the captured stderr.
        """
        # Only an exited subprocess can have "died" — a live session that
        # surfaces the marker some other way is not this failure mode.
        if bool(getattr(session, 'is_alive', False)):
            return False
        marker = 'previous_message_id'
        terminal = getattr(session, 'terminal_event', None)
        if terminal is not None:
            raw = getattr(terminal, 'raw', {}) or {}
            if bool(raw.get('is_error', False)) \
                    and marker in str(raw.get('result', '') or ''):
                return True
        try:
            stderr_lines = session.stderr_snapshot()
        except Exception:
            stderr_lines = []
        return any(marker in line for line in stderr_lines)

    @staticmethod
    def _normalize_task_id(task_id: str) -> str:
        # Strip whitespace, PRESERVE original case. This value is what
        # gets stored on ``record.task_id`` so error messages, audit
        # logs, and the on-disk record's display field match what the
        # ticket system uses (e.g. ``PROJ-1``).
        normalized = str(task_id or '').strip()
        if not normalized:
            raise ValueError('task_id is required')
        return normalized

    @staticmethod
    def _lookup_key(task_id: str) -> str:
        # Canonical key for the in-memory dicts (``_records``, ``_sessions``,
        # ``_spawn_locks``). Deliberately the SAME function the on-disk
        # filename uses (``record_key``): if the memory key and the file key
        # ever diverged, a lookup would miss a record that is right there on
        # disk. Lowercased because two casings otherwise produce two records
        # on a case-sensitive filesystem and a silent overwrite on macOS.
        return record_key(ClaudeSessionManager._normalize_task_id(task_id))


    def _persist_record(self, record: AgentSessionRecord) -> None:
        """Write the record, then mirror it into the workspace metadata.

        Storage rules (naming, atomicity, legacy-case cleanup) are shared —
        see ``agent_core_lib.session.record_files``. The workspace mirror is
        this host's concern and stays here.
        """
        write_record(self._state_dir, record, logger=self.logger)
        self._mirror_to_workspace_metadata(record)

    def _mirror_to_workspace_metadata(self, record: AgentSessionRecord) -> None:
        if self._workspace_manager is None:
            return
        if not has_session_id(record.agent_session_id) and not record.cwd:
            return
        try:
            self._workspace_manager.update_agent_session(
                record.task_id,
                agent_session_id=fix_session_id(record.agent_session_id),
                cwd=record.cwd,
            )
        except Exception:
            self.logger.exception(
                'failed to mirror claude session id to workspace metadata for task %s',
                record.task_id,
            )

    def _forget_claude_transcript(self, record, task_id: str) -> None:
        """Delete the Claude CLI transcripts for a forgotten task.

        Called only on the ``remove_record=True`` path (task done /
        closed / operator forget). The workspace clones + the orchestrator
        session record are already gone by here; the Claude
        transcripts under ``~/.claude/projects/`` would otherwise
        accumulate forever. Covers the ACTIVE chat and every detached
        previous chat — multi-chat tasks would otherwise leak one
        JSONL per old conversation. Best-effort — a unlink failure
        must not break ``terminate_session``.
        """
        active_id = read_session_id_from(record)
        previous_ids = session_id_list(
            getattr(record, 'previous_session_ids', None),
        )
        all_ids = ([active_id] if active_id else []) + [
            sid for sid in previous_ids if sid != active_id
        ]
        if not all_ids:
            return
        from claude_core_lib.claude_core_lib.session.history import (
            delete_session_file,
        )
        for agent_session_id in all_ids:
            try:
                if delete_session_file(agent_session_id):
                    self.logger.info(
                        'deleted Claude transcript %s for forgotten task %s',
                        agent_session_id,
                        task_id,
                    )
            except Exception:
                self.logger.exception(
                    'failed deleting Claude transcript %s for task %s',
                    agent_session_id,
                    task_id,
                )



    def _with_refreshed_session_id(
        self,
        record: AgentSessionRecord | None,
    ) -> AgentSessionRecord | None:
        if record is None:
            return None
        lookup_key = self._lookup_key(record.task_id)
        session = self._sessions.get(lookup_key)
        if session is None:
            return record
        if getattr(session, 'is_alive', False):
            drifted = self._discard_if_session_id_drifted_locked(
                lookup_key, record.task_id, session,
            )
            if drifted:
                return record
        live_id = read_session_id_from(session)
        record_id = read_session_id_from(record)
        if has_session_id(live_id) and not same_session_id(live_id, record_id):
            if record_id:
                self.logger.warning(
                    'task %s: live Claude reports session id %s, but '
                    'record is pinned to %s; keeping the persisted id',
                    record.task_id, live_id, record_id,
                )
                return record
            record.agent_session_id = live_id
            record.updated_at_epoch = time.time()
            self._persist_record(record)
        return record

    def _discard_if_session_id_drifted_locked(
        self,
        lookup_key: str,
        normalized_task_id: str,
        session: StreamingClaudeSession,
    ) -> bool:
        if getattr(session, 'resume_was_ignored', False):
            # The init event (possibly arriving AFTER the spawn-time
            # verdict window) revealed that --resume was silently
            # ignored: this live process is a fresh, memoryless
            # conversation masquerading as the resumed one. Drop and
            # terminate it; the pinned id stays so the next spawn
            # resumes the real history.
            self._sessions.pop(lookup_key, None)
            self.logger.warning(
                'task %s: live claude session ignored --resume and is '
                'running a fresh blank conversation; terminating it so '
                'the next spawn can resume the pinned id',
                normalized_task_id,
            )
            try:
                session.terminate()
            except Exception:
                self.logger.exception(
                    'failed to terminate memoryless session for task %s',
                    normalized_task_id,
                )
            return True
        record = self._records.get(lookup_key)
        pinned_id = read_session_id_from(record)
        if not pinned_id:
            return False
        live_id = read_session_id_from(session)
        if same_session_id(live_id, pinned_id):
            return False
        self._sessions.pop(lookup_key, None)
        self.logger.warning(
            'task %s: live Claude session id %s disagrees with pinned id %s; '
            'terminating live process so the next spawn resumes the pinned id',
            normalized_task_id, live_id or '(blank)', pinned_id,
        )
        try:
            session.terminate()
        except Exception:
            self.logger.exception(
                'failed to terminate mismatched live session for task %s',
                normalized_task_id,
            )
        return True
