"""Long-lived `claude -p` stream-json subprocess wrapper.

Unlike :class:`kato.client.claude.cli_client.ClaudeCliClient` (one-shot,
single prompt → single result), this wrapper keeps the Claude CLI process
alive for the duration of a planning conversation: events stream out as
NDJSON, follow-up user messages stream in. :class:`ClaudeSessionManager`
maps each session 1-to-1 with a Kato task so a human can chat with Claude
via the planning UI and approve permission asks mid-task.

This module is transport only — no agent_service / orchestration coupling.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from queue import Empty, Queue
from typing import Any

from kato.client.claude.wire_protocol import (
    CLAUDE_EVENT_CONTROL_REQUEST,
    CLAUDE_EVENT_CONTROL_RESPONSE,
    CLAUDE_EVENT_PERMISSION_RESPONSE,
    CLAUDE_EVENT_RESULT,
    CLAUDE_EVENT_SYSTEM,
    CLAUDE_SYSTEM_SUBTYPE_INIT,
    PERMISSION_REQUEST_EVENT_TYPES,
)
from kato.helpers.architecture_doc_utils import read_architecture_doc
from kato.helpers.logging_utils import configure_logger
from kato.helpers.text_utils import condensed_text, normalized_text


def _wait_for_exit(proc: subprocess.Popen, timeout: float) -> bool:
    """Block up to ``timeout`` seconds for ``proc`` to exit. True on exit."""
    try:
        proc.wait(timeout=timeout)
        return True
    except subprocess.TimeoutExpired:
        return False


@dataclass
class SessionEvent(object):
    """One NDJSON event produced by the Claude CLI on stdout."""

    raw: dict[str, Any] = field(default_factory=dict)
    received_at_epoch: float = field(default_factory=time.time)

    @property
    def event_type(self) -> str:
        return str(self.raw.get('type', '') or '')

    @property
    def subtype(self) -> str:
        return str(self.raw.get('subtype', '') or '')

    @property
    def is_terminal(self) -> bool:
        # Claude CLI emits exactly one final `{"type": "result", ...}` event.
        return self.event_type == CLAUDE_EVENT_RESULT

    def to_dict(self) -> dict[str, Any]:
        return {
            'received_at_epoch': self.received_at_epoch,
            'raw': self.raw,
        }


class StreamingClaudeSession(object):
    """Long-lived `claude -p --output-format stream-json` subprocess.

    Threading model:
      - One reader thread parses stdout NDJSON and enqueues SessionEvents.
      - One reader thread drains stderr into the logger (best-effort).
      - All public methods are thread-safe; the consumer (webserver) calls
        them from request handlers / WebSocket loops.

    The wrapper does NOT block on the subprocess in start(); it returns as
    soon as the process is launched. Use ``events_iter()`` or
    ``poll_event(timeout)`` to consume events as they arrive. Call
    ``terminate()`` for clean shutdown.
    """

    DEFAULT_BINARY = 'claude'
    DEFAULT_PERMISSION_MODE = 'acceptEdits'
    # When the agent runs in a non-bypass permission mode it will pause and
    # ask before invoking a tool. The `stdio` permission-prompt tool routes
    # those asks back as `permission_request` events on stdout (which the
    # webserver forwards to the planning UI) and reads the user's
    # `permission_response` envelopes from stdin.
    DEFAULT_PERMISSION_PROMPT_TOOL = 'stdio'
    STDERR_LOG_INTERVAL_SECONDS = 0.5

    def __init__(
        self,
        *,
        task_id: str,
        binary: str = '',
        cwd: str = '',
        model: str = '',
        permission_mode: str = '',
        permission_prompt_tool: str = '',
        allowed_tools: str = '',
        disallowed_tools: str = '',
        max_turns: int | None = None,
        resume_session_id: str = '',
        env: dict[str, str] | None = None,
        effort: str = '',
        architecture_doc_path: str = '',
        done_callback=None,
    ) -> None:
        if not str(task_id or '').strip():
            raise ValueError('task_id is required for a streaming session')
        self._task_id = str(task_id).strip()
        self._binary = normalized_text(binary) or self.DEFAULT_BINARY
        self._cwd = normalized_text(cwd) or os.getcwd()
        self._model = normalized_text(model)
        self._permission_mode = normalized_text(permission_mode) or self.DEFAULT_PERMISSION_MODE
        normalized_prompt_tool = normalized_text(permission_prompt_tool)
        if normalized_prompt_tool:
            self._permission_prompt_tool = normalized_prompt_tool
        elif self._permission_mode == 'bypassPermissions':
            # Fully autonomous: nothing will be asked anyway.
            self._permission_prompt_tool = ''
        else:
            # Default for any non-bypass mode: route permission asks back
            # over stdio so the planning UI can intercept them.
            self._permission_prompt_tool = self.DEFAULT_PERMISSION_PROMPT_TOOL
        self._allowed_tools = normalized_text(allowed_tools)
        self._disallowed_tools = normalized_text(disallowed_tools)
        self._max_turns = max_turns
        self._effort = normalized_text(effort).lower()
        self._resume_session_id = normalized_text(resume_session_id)
        self._architecture_doc_path = normalized_text(architecture_doc_path)
        self._env_overrides = dict(env or {})
        # Callback fired once when an assistant message arrives that
        # contains the ``KATO_TASK_DONE_SENTINEL`` token. Wired by the
        # session manager to ``AgentService.finish_task_planning_session``
        # so Claude can end the chat by emitting the magic string.
        # ``_done_sentinel_fired`` guards against re-firing on later
        # messages that quote the sentinel back.
        self._done_callback = done_callback
        self._done_sentinel_fired = False

        self._proc: subprocess.Popen[bytes] | None = None
        self._proc_lock = threading.Lock()
        self._stdin_lock = threading.Lock()
        self._event_queue: Queue[SessionEvent] = Queue()
        # Per-request payload cache for the ``control_request`` /
        # ``control_response`` flow: when the CLI asks "can I run tool X?"
        # the response must echo the original ``input`` back as
        # ``updatedInput`` (allow case) so the tool runs with the same
        # arguments the agent intended. Cleared once a response is sent.
        self._pending_control_requests: dict[str, dict[str, Any]] = {}
        self._pending_control_requests_lock = threading.Lock()
        # Full per-session history. Browsers join late and need to replay
        # everything; the orchestration also reads through it. Memory grows
        # linearly with events, which is fine for the bounded lifetime of a
        # planning task. A bounded deque was a footgun: once full, len()
        # stayed constant and the WS loop stopped forwarding new events.
        self._recent_events: list[SessionEvent] = []
        self._recent_events_lock = threading.Lock()
        self._claude_session_id: str = ''
        self._terminal_event: SessionEvent | None = None
        self._reader_threads: list[threading.Thread] = []
        self._stderr_lines: list[str] = []
        self._stderr_lock = threading.Lock()
        self.logger = configure_logger(self.__class__.__name__)
        if self._permission_mode == 'bypassPermissions':
            self.logger.warning(
                'KATO_CLAUDE_BYPASS_PERMISSIONS=true: streaming Claude session '
                'for task %s will run with --permission-mode bypassPermissions. '
                'The planning UI will not intercept tool calls — the agent can '
                'run Bash, Edit, Write, and any other tool without asking. '
                'The operator who set this flag accepts responsibility for any '
                'harm caused by the agent. See SECURITY.md.',
                self._task_id,
            )

    # ----- properties -----

    @property
    def task_id(self) -> str:
        return self._task_id

    @property
    def cwd(self) -> str:
        return self._cwd

    @property
    def claude_session_id(self) -> str:
        return self._claude_session_id

    @property
    def is_alive(self) -> bool:
        with self._proc_lock:
            return self._proc is not None and self._proc.poll() is None

    @property
    def is_working(self) -> bool:
        """True when Claude is mid-turn — has spoken but not finalized.

        Mirrors the planning UI's ``turnInFlight`` reducer, so the tab
        dot in the sidebar can dim once a turn closes (``result`` event)
        even when the subprocess stays alive for the next message. Walks
        the event log from the newest end:

        - first ``result`` → turn closed, not working,
        - first ``assistant`` / ``stream_event`` / ``user`` → mid-turn,
        - only ``system`` events → idle (just spawned, no work yet).
        """
        if not self.is_alive:
            return False
        with self._recent_events_lock:
            for event in reversed(self._recent_events):
                event_type = event.event_type
                if event_type == CLAUDE_EVENT_RESULT:
                    return False
                if event_type in ('assistant', 'stream_event', 'user'):
                    return True
        return False

    @property
    def has_finished(self) -> bool:
        return self._terminal_event is not None

    @property
    def terminal_event(self) -> SessionEvent | None:
        return self._terminal_event

    # ----- lifecycle -----

    def start(self, initial_prompt: str = '') -> None:
        """Launch the subprocess and (optionally) send the first user message."""
        with self._proc_lock:
            if self._proc is not None:
                raise RuntimeError(
                    f'streaming session for task {self._task_id} already started'
                )
            command = self._build_command()
            env = self._build_env()
            # Bypass mode wraps the spawn in the hardened Docker
            # sandbox — see ``kato.sandbox.manager``. The container
            # bind-mounts the workspace, blocks egress to anything but
            # api.anthropic.com, and runs Claude as a non-root user
            # with no capabilities. The stdin/stdout NDJSON contract
            # is unchanged; reader threads don't care that the other
            # end is a docker process.
            spawn_cwd: str | None = self._cwd
            if self._permission_mode == 'bypassPermissions':
                from kato.sandbox.manager import (
                    SandboxError,
                    ensure_image,
                    make_container_name,
                    record_spawn,
                    wrap_command,
                )
                try:
                    ensure_image(logger=self.logger)
                except SandboxError as exc:
                    raise RuntimeError(
                        f'failed to prepare Claude sandbox image: {exc}',
                    ) from exc
                container_name = make_container_name(self._task_id)
                # Pre-spawn workspace check — refuse (don't just warn)
                # if the operator's repo contains files that look like
                # committed credentials. Operator can override via
                # KATO_SANDBOX_ALLOW_WORKSPACE_SECRETS=true if these
                # are intentional repo fixtures.
                from kato.sandbox.manager import enforce_no_workspace_secrets
                try:
                    enforce_no_workspace_secrets(self._cwd, logger=self.logger)
                except SandboxError as exc:
                    raise RuntimeError(
                        f'sandbox spawn blocked: {exc}',
                    ) from exc
                command = wrap_command(
                    command,
                    workspace_path=self._cwd,
                    container_name=container_name,
                )
                # Audit-log this spawn before the subprocess actually
                # starts so the operator has a record even if the
                # container fails to come up.
                record_spawn(
                    task_id=self._task_id,
                    container_name=container_name,
                    workspace_path=self._cwd,
                    logger=self.logger,
                )
                # Docker sets the container WORKDIR to /workspace; the
                # host cwd is irrelevant for the docker client itself.
                spawn_cwd = None
            try:
                self._proc = subprocess.Popen(
                    command,
                    cwd=spawn_cwd,
                    env=env,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    bufsize=0,  # unbuffered: we want each NDJSON line ASAP
                )
            except (OSError, FileNotFoundError) as exc:
                raise RuntimeError(
                    f'failed to launch claude CLI binary "{self._binary}": {exc}'
                ) from exc
            self.logger.info(
                'started streaming claude session for task %s (pid %s)',
                self._task_id,
                self._proc.pid,
            )
            self._spawn_reader_threads()

        if initial_prompt:
            self.send_user_message(initial_prompt)

    def send_user_message(self, text: str) -> None:
        """Push a follow-up user message into the live conversation."""
        normalized = str(text or '').rstrip('\n')
        if not normalized:
            return
        if not self.is_alive:
            raise RuntimeError(
                f'cannot send to streaming session for task {self._task_id}: '
                'subprocess is not running'
            )
        envelope = {
            'type': 'user',
            'message': {
                'role': 'user',
                'content': [{'type': 'text', 'text': normalized}],
            },
        }
        self._write_stdin_line(envelope)
        self.logger.info(
            'forwarded user message to claude session for task %s (%s chars)',
            self._task_id,
            len(normalized),
        )

    def send_permission_response(
        self,
        request_id: str,
        allow: bool,
        rationale: str = '',
    ) -> None:
        """Reply to a ``control_request`` permission ask from the agent.

        Builds the envelope shape that ``--permission-prompt-tool stdio``
        expects: ``control_response`` wrapping a ``response`` body whose
        inner ``response`` carries the actual decision. ``allow`` echoes
        the original tool input back as ``updatedInput`` so the tool
        runs with the agent's intended arguments; ``deny`` carries an
        optional rationale Claude can read back.
        """
        request_id_str = str(request_id or '').strip()
        if not request_id_str:
            raise ValueError('request_id is required')
        with self._pending_control_requests_lock:
            request = self._pending_control_requests.pop(request_id_str, {})
        original_input = (
            request.get('input') if isinstance(request, dict) else {}
        ) or {}
        if allow:
            decision = {'behavior': 'allow', 'updatedInput': original_input}
        else:
            decision = {
                'behavior': 'deny',
                'message': normalized_text(rationale) or 'denied by user',
            }
        envelope = {
            'type': CLAUDE_EVENT_CONTROL_RESPONSE,
            'response': {
                'subtype': 'success',
                'request_id': request_id_str,
                'response': decision,
            },
        }
        self._write_stdin_line(envelope)
        # Mirror the response into the event log so any browser that
        # reconnects (or another tab opened on the same task) replays a
        # signal that this request is no longer pending — otherwise the
        # backlog would re-pop the modal for an already-answered ask.
        synthetic_event = SessionEvent(
            raw={
                'type': CLAUDE_EVENT_PERMISSION_RESPONSE,
                'request_id': request_id_str,
                'allow': bool(allow),
            },
        )
        with self._recent_events_lock:
            self._recent_events.append(synthetic_event)
        self._event_queue.put(synthetic_event)

    def terminate(self, grace_seconds: float = 5.0) -> None:
        """Close stdin, wait briefly, then SIGTERM / kill as needed.

        Three-step escalation: each step gives the subprocess a chance to
        exit cleanly before the next, more forceful one. We hold the proc
        lock for the whole sequence so a concurrent ``start`` can't race.
        """
        with self._proc_lock:
            proc = self._proc
            if proc is None:
                return
            self._close_stdin_locked()
            if not _wait_for_exit(proc, max(0.1, float(grace_seconds))):
                self._escalate_to_sigterm(proc)
            self._proc = None
        for thread in self._reader_threads:
            thread.join(timeout=1.0)
        self._reader_threads = []

    def _escalate_to_sigterm(self, proc: subprocess.Popen) -> None:
        self.logger.info(
            'streaming claude session for task %s did not exit; sending SIGTERM',
            self._task_id,
        )
        self._send_signal_locked(signal.SIGTERM)
        if _wait_for_exit(proc, 2.0):
            return
        self._escalate_to_kill(proc)

    def _escalate_to_kill(self, proc: subprocess.Popen) -> None:
        self.logger.warning(
            'streaming claude session for task %s ignored SIGTERM; killing',
            self._task_id,
        )
        # ``Popen.kill()`` is portable: SIGKILL on POSIX, ``TerminateProcess``
        # on Windows. ``signal.SIGKILL`` itself doesn't exist on Windows.
        try:
            proc.kill()
        except (ProcessLookupError, OSError):
            pass
        _wait_for_exit(proc, 2.0)

    # ----- event consumption -----

    def poll_event(self, timeout: float = 0.0) -> SessionEvent | None:
        """Pop the next event if one is available, optionally waiting."""
        try:
            return self._event_queue.get(timeout=max(0.0, float(timeout)))
        except Empty:
            return None

    def events_iter(self) -> Iterator[SessionEvent]:
        """Yield events as they arrive; ends when the session terminates."""
        while self.is_alive or not self._event_queue.empty():
            try:
                event = self._event_queue.get(timeout=0.25)
            except Empty:
                continue
            yield event
            if event.is_terminal:
                return

    def recent_events(self, limit: int | None = None) -> list[SessionEvent]:
        """Snapshot of every event received so far (oldest first)."""
        with self._recent_events_lock:
            events = list(self._recent_events)
        if limit is not None and limit >= 0:
            events = events[-limit:]
        return events

    def stderr_snapshot(self) -> list[str]:
        with self._stderr_lock:
            return list(self._stderr_lines)

    # ----- internals -----

    def _build_command(self) -> list[str]:
        binary_path = shutil.which(self._binary) or self._binary
        command: list[str] = [
            binary_path,
            '-p',
            '--output-format', 'stream-json',
            '--input-format', 'stream-json',
            '--verbose',
            # NOTE: deliberately NOT passing --include-partial-messages.
            # That flag fires a `stream_event` envelope per token delta and
            # the planning UI is happier rendering full assistant messages
            # at once. Re-enable here only if you also teach the JS
            # renderer to accumulate deltas into a live bubble.
            '--permission-mode', self._permission_mode,
        ]
        if self._permission_prompt_tool:
            command.extend(['--permission-prompt-tool', self._permission_prompt_tool])
        if self._model:
            command.extend(['--model', self._model])
        if self._max_turns is not None and self._max_turns > 0:
            command.extend(['--max-turns', str(self._max_turns)])
        if self._effort:
            command.extend(['--effort', self._effort])
        if self._allowed_tools:
            command.extend(['--allowedTools', self._allowed_tools])
        # Hard, non-overridable git denylist. Kato is the only component
        # that ever runs git operations; Claude must NEVER invoke `git`
        # directly. See ClaudeCliClient.GIT_DENY_PATTERNS for rationale.
        from kato.client.claude.cli_client import ClaudeCliClient as _CliClient
        merged_disallowed = _CliClient._merge_disallowed_with_git_deny(
            self._disallowed_tools
        )
        command.extend(['--disallowedTools', merged_disallowed])
        architecture_doc = read_architecture_doc(
            self._architecture_doc_path, logger=self.logger,
        )
        if architecture_doc:
            command.extend(['--append-system-prompt', architecture_doc])
        if self._resume_session_id:
            command.extend(['--resume', self._resume_session_id])
        else:
            # Pin a session-id up front so callers can resume after restart
            # without waiting for the system event to arrive.
            self._claude_session_id = str(uuid.uuid4())
            command.extend(['--session-id', self._claude_session_id])
        return command

    def _build_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env.update(self._env_overrides)
        env.setdefault('CLAUDE_CODE_NONINTERACTIVE', '1')
        return env

    def _spawn_reader_threads(self) -> None:
        stdout_thread = threading.Thread(
            target=self._stdout_reader_loop,
            name=f'claude-session-stdout-{self._task_id}',
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=self._stderr_reader_loop,
            name=f'claude-session-stderr-{self._task_id}',
            daemon=True,
        )
        self._reader_threads = [stdout_thread, stderr_thread]
        stdout_thread.start()
        stderr_thread.start()

    def _stdout_reader_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        for raw_line in iter(proc.stdout.readline, b''):
            text = raw_line.decode('utf-8', errors='replace').rstrip('\n')
            if not text:
                continue
            event = self._parse_stdout_line(text)
            if event is None:
                continue
            with self._recent_events_lock:
                self._recent_events.append(event)
            self._event_queue.put(event)
            self._maybe_capture_session_id(event)
            self._maybe_capture_control_request(event)
            self._maybe_fire_done_sentinel(event)
            self._log_event_for_operator(event)
            if event.is_terminal:
                self._terminal_event = event
                # Don't break here — let the subprocess close stdout itself.
        # stdout closed; the subprocess is winding down or already gone.

    @staticmethod
    def _permission_request_details(event: SessionEvent) -> tuple[str, str]:
        """Pull tool_name and request_id from either of the two CLI shapes.

        Older ``permission_request`` events put fields at top level;
        ``control_request`` (used by ``--permission-prompt-tool stdio``)
        nests them under ``request``.
        """
        raw = event.raw or {}
        request = raw.get('request') if isinstance(raw.get('request'), dict) else {}
        tool_name = (
            str(raw.get('tool_name', '') or '')
            or str(raw.get('tool', '') or '')
            or str(request.get('tool_name', '') or '')
            or str(request.get('tool', '') or '')
            or 'tool'
        )
        request_id = (
            str(raw.get('request_id', '') or '')
            or str(raw.get('id', '') or '')
            or '?'
        )
        return tool_name, request_id

    def _maybe_fire_done_sentinel(self, event: SessionEvent) -> None:
        """Detect ``<KATO_TASK_DONE>`` in an assistant text block and fire once.

        The wait-planning prompt instructs Claude to end its final
        message with this exact token when work is complete. We scan
        every assistant text block, but fire the callback at most
        once per session — if Claude later quotes the sentinel back
        in an apology/correction message, we ignore it. Failures in
        the callback are logged and never propagate so a flaky
        publish path can't crash the reader thread.
        """
        if self._done_sentinel_fired or self._done_callback is None:
            return
        if event.event_type != 'assistant':
            return
        message = event.raw.get('message') if isinstance(event.raw, dict) else None
        if not isinstance(message, dict):
            return
        content = message.get('content')
        if not isinstance(content, list):
            return
        from kato.data_layers.data.sentinels import KATO_TASK_DONE_SENTINEL
        for block in content:
            if not isinstance(block, dict) or block.get('type') != 'text':
                continue
            text = str(block.get('text', '') or '')
            if KATO_TASK_DONE_SENTINEL in text:
                self._done_sentinel_fired = True
                self.logger.info(
                    'task %s: detected %s in assistant message — '
                    'firing done callback',
                    self._task_id, KATO_TASK_DONE_SENTINEL,
                )
                try:
                    self._done_callback(self._task_id)
                except Exception:
                    self.logger.exception(
                        'done callback failed for task %s',
                        self._task_id,
                    )
                return

    def _maybe_capture_control_request(self, event: SessionEvent) -> None:
        """Store ``control_request`` payloads so we can echo ``updatedInput``."""
        if event.event_type != CLAUDE_EVENT_CONTROL_REQUEST:
            return
        request_id = str(event.raw.get('request_id', '') or '').strip()
        if not request_id:
            return
        request = event.raw.get('request') or {}
        if not isinstance(request, dict):
            return
        with self._pending_control_requests_lock:
            self._pending_control_requests[request_id] = request

    def _log_event_for_operator(self, event: SessionEvent) -> None:
        """Surface high-signal events to the kato terminal log.

        The planning UI shows everything; the operator running kato wants
        only the moments that need their attention. Today that's
        permission requests (the agent has paused waiting for an Allow /
        Deny click) and result events (turn completed).
        """
        event_type = event.event_type
        if event_type in PERMISSION_REQUEST_EVENT_TYPES:
            tool_name, request_id = self._permission_request_details(event)
            self.logger.info(
                'task %s: claude is asking permission to run %s '
                '(request_id=%s) — open the planning UI to approve or deny',
                self._task_id,
                tool_name,
                request_id,
            )
        elif event_type == CLAUDE_EVENT_RESULT:
            is_error = bool(event.raw.get('is_error', False))
            result_text = condensed_text(event.raw.get('result', ''))[:160]
            stderr_tail = self.stderr_snapshot()[-10:] if is_error else []
            # Silence the transient error from a stale --resume id:
            # the session manager auto-recovers by spawning a fresh
            # session, so logging "(error)" + a stack-of-stderr just
            # confuses the operator. The recovery itself logs a clear
            # "rejected resume id ... retrying" line.
            if is_error and self._stderr_indicates_stale_resume(stderr_tail):
                self.logger.debug(
                    'task %s: claude rejected resume id %s (will be auto-healed)',
                    self._task_id,
                    self._resume_session_id,
                )
                return
            self.logger.info(
                'task %s: claude turn ended (%s)%s',
                self._task_id,
                'error' if is_error else 'success',
                f': {result_text}' if result_text else '',
            )
            if is_error and stderr_tail:
                # Surface whatever the CLI wrote to stderr so the operator
                # can see why Claude bailed (auth, rate-limit, missing tool,
                # etc). Without this the only visible signal is "(error)"
                # and a wait-planning loop becomes opaque.
                self.logger.warning(
                    'task %s: claude stderr (last %d lines):\n%s',
                    self._task_id,
                    len(stderr_tail),
                    '\n'.join(stderr_tail),
                )

    def _stderr_indicates_stale_resume(self, stderr_lines: list) -> bool:
        if not self._resume_session_id:
            return False
        marker = f'No conversation found with session ID: {self._resume_session_id}'
        return any(marker in line for line in stderr_lines)

    def _stderr_reader_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        for raw_line in iter(proc.stderr.readline, b''):
            text = raw_line.decode('utf-8', errors='replace').rstrip('\n')
            if not text:
                continue
            with self._stderr_lock:
                self._stderr_lines.append(text)
                if len(self._stderr_lines) > 500:
                    self._stderr_lines = self._stderr_lines[-500:]
            self.logger.debug(
                'streaming claude session %s stderr: %s',
                self._task_id,
                condensed_text(text)[:240],
            )

    def _parse_stdout_line(self, text: str) -> SessionEvent | None:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            self.logger.warning(
                'streaming claude session %s emitted non-JSON line: %s',
                self._task_id,
                condensed_text(text)[:240],
            )
            return None
        if not isinstance(payload, dict):
            return None
        return SessionEvent(raw=payload)

    def _maybe_capture_session_id(self, event: SessionEvent) -> None:
        if self._claude_session_id:
            return
        candidate = str(event.raw.get('session_id', '') or '').strip()
        if candidate:
            self._claude_session_id = candidate

    def _write_stdin_line(self, envelope: dict[str, Any]) -> None:
        line = (json.dumps(envelope) + '\n').encode('utf-8')
        with self._stdin_lock, self._proc_lock:
            if self._proc is None or self._proc.stdin is None or self._proc.poll() is not None:
                raise RuntimeError(
                    f'cannot write to streaming session for task {self._task_id}: stdin closed'
                )
            try:
                self._proc.stdin.write(line)
                self._proc.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise RuntimeError(
                    f'streaming session for task {self._task_id} stdin broke: {exc}'
                ) from exc

    def _close_stdin_locked(self) -> None:
        if self._proc is None or self._proc.stdin is None:
            return
        try:
            self._proc.stdin.close()
        except Exception:
            pass

    def _send_signal_locked(self, sig: int) -> None:
        if self._proc is None:
            return
        try:
            self._proc.send_signal(sig)
        except (ProcessLookupError, OSError):
            pass
