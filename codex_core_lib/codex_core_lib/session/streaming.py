"""An interactive Codex chat, built on a CLI that has no interactive mode.

``codex exec`` is ONE-SHOT: it takes a prompt, streams JSONL events while it
works, and exits. There is no persistent stdin to write a second message to.
Continuity comes from ``codex exec resume <thread_id>``, which starts a fresh
process that remembers the earlier conversation.

So a chat here is not "a subprocess with a long life" — it is a small state
machine that spawns one process PER TURN and stitches their event streams into
a single log the UI can tail. Concretely:

* the first turn runs ``codex exec`` and learns the ``thread_id`` from the
  ``thread.started`` event; that id is this chat's identity from then on;
* every later turn runs ``codex exec resume <thread_id>``;
* between turns there is no process at all, and the chat is still alive.

That last point is the one thing a reader coming from the Claude session must
recalibrate: ``is_alive`` here means "this chat can take another message", not
"a process is running". Under a per-turn model the process-liveness reading
would be False between every single turn, which would tell every caller the
chat was dead. See ``agent_provider_contracts.agent_session``.

What this model gives up: nothing can be sent MID-turn (there is no stdin to
write to), so an operator message that arrives while a turn is running is
queued and sent when it finishes. What it gains: a wedged CLI cannot block
anything — the process either finishes or is killed, and the next turn starts
clean.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from dataclasses import dataclass, field
from queue import Empty, Queue
from typing import Any

from agent_core_lib.agent_core_lib.helpers.logging_utils import configure_logger
from utils_core_lib.utils_core_lib.text_utils import normalized_text

#: Emitted once per turn when the process starts; carries the thread id that
#: becomes this chat's ``agent_session_id``.
CODEX_EVENT_THREAD_STARTED = 'thread.started'
#: Emitted once at the end of a turn. This is the terminal event the UI's
#: in-flight indicator clears on.
CODEX_EVENT_TURN_COMPLETED = 'turn.completed'
#: Synthesised by this module (not by the CLI) when a turn ends without a
#: ``turn.completed`` — a crash, a kill, a non-zero exit. Without it the UI
#: would show a turn as running forever.
CODEX_EVENT_TURN_ABORTED = 'turn.aborted'
#: Emitted BY THE CLI when the turn reached the model and the model (or the
#: API) refused it — a bad model name, an auth problem, a rate limit. It is
#: terminal: the process exits straight after.
#:
#: Missing from this set, every such failure fell through to the synthesised
#: ``turn.aborted`` above, which threw the CLI's own explanation away. An
#: operator whose model picker still held a Claude alias saw a bare
#: "turn.aborted" instead of "The 'opus' model is not supported when using
#: Codex with a ChatGPT account" — the one line that says what to change.
CODEX_EVENT_TURN_FAILED = 'turn.failed'

_MAX_RECENT_EVENTS = 5000
_MAX_STDERR_LINES = 200


@dataclass
class SessionEvent(object):
    """One JSONL event from ``codex exec``.

    Deliberately the same shape as the Claude transport's event: the webserver
    serialises both through ``to_dict()`` and the SSE stream carries whatever
    the transport produced. Keeping the envelope identical is what lets one UI
    tail either backend.
    """

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
        return self.event_type in (
            CODEX_EVENT_TURN_COMPLETED,
            CODEX_EVENT_TURN_ABORTED,
            CODEX_EVENT_TURN_FAILED,
        )

    def to_dict(self) -> dict[str, Any]:
        return {'received_at_epoch': self.received_at_epoch, 'raw': self.raw}


class StreamingCodexSession(object):
    """A chat with Codex: one process per turn, one continuous event log."""

    def __init__(
        self,
        *,
        task_id: str,
        cwd: str = '',
        binary: str = 'codex',
        model: str = '',
        agent_session_id: str = '',
        additional_dirs: tuple[str, ...] | list[str] = (),
        sandbox_mode: str = 'workspace-write',
        build_command=None,
        build_env=None,
        logger=None,
    ) -> None:
        self._task_id = str(task_id or '')
        self._cwd = str(cwd or '')
        self._binary = str(binary or 'codex')
        self._model = str(model or '')
        # Non-empty when resuming an existing conversation — either a chat the
        # operator returned to, or one rehydrated after a host restart.
        self._agent_session_id = normalized_text(agent_session_id)
        self._additional_dirs = tuple(str(d) for d in (additional_dirs or ()) if d)
        self._sandbox_mode = str(sandbox_mode or 'workspace-write')
        self._build_command = build_command or self._default_command
        self._build_env = build_env
        self.logger = logger or configure_logger('StreamingCodexSession')

        self._events: list[SessionEvent] = []
        self._events_lock = threading.Lock()
        self._event_queue: Queue = Queue()
        self._events_changed = threading.Condition()
        self._stderr_lines: list[str] = []
        self._stderr_lock = threading.Lock()

        self._proc: subprocess.Popen | None = None
        self._turn_lock = threading.Lock()
        self._terminated = False
        self._turn_thread: threading.Thread | None = None
        self._terminal_event: SessionEvent | None = None
        # Messages that arrived mid-turn. There is no stdin to interrupt, so
        # they wait rather than being dropped — dropping an operator's message
        # is the one outcome worse than a delay.
        self._pending_messages: list[str] = []
        self._session_id_correction_callback = None

    # ----- identity -------------------------------------------------------

    @property
    def task_id(self) -> str:
        return self._task_id

    @property
    def cwd(self) -> str:
        return self._cwd

    @property
    def agent_session_id(self) -> str:
        return self._agent_session_id

    @property
    def is_alive(self) -> bool:
        """Can this chat still take a message?

        True between turns, when no process exists at all — see the module
        docstring. Only ``terminate`` makes a chat un-alive.
        """
        return not self._terminated

    @property
    def is_working(self) -> bool:
        """True while a turn's process is running."""
        proc = self._proc
        return proc is not None and proc.poll() is None

    def allowed_additional_dirs(self) -> list[str]:
        return list(self._additional_dirs)

    # ----- lifecycle ------------------------------------------------------

    def start(self, initial_prompt: str = '') -> None:
        """Bring the chat up. A turn runs only if there is something to say.

        Unlike a persistent-process transport there is nothing to "launch" on
        its own: with no prompt this just marks the chat usable, and the first
        real turn spawns the first process.
        """
        self._terminated = False
        prompt = str(initial_prompt or '').strip()
        if prompt:
            self.send_user_message(prompt)

    def send_user_message(self, text: str, **kwargs: Any) -> None:
        """Run one turn with ``text``. Returns as soon as it is underway.

        Mid-turn messages queue: ``codex exec`` has no stdin to interrupt, so
        the alternative would be dropping what the operator typed.
        """
        del kwargs  # accepted for parity with the persistent-process transport
        message = str(text or '').strip()
        if not message:
            return
        if self._terminated:
            raise RuntimeError(
                f'chat for task {self._task_id} has been terminated'
            )
        with self._turn_lock:
            if self.is_working:
                self._pending_messages.append(message)
                self.logger.info(
                    'task %s: turn in flight; queued the message (%d waiting)',
                    self._task_id, len(self._pending_messages),
                )
                return
            self._start_turn_locked(message)

    def terminate(self, grace_seconds: float = 1.0) -> None:
        """End the chat and kill any in-flight turn. Safe to call twice."""
        self._terminated = True
        proc = self._proc
        self._proc = None
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=max(0.1, float(grace_seconds)))
                except subprocess.TimeoutExpired:
                    proc.kill()
            except OSError:
                pass
        with self._events_changed:
            self._events_changed.notify_all()

    # ----- events ---------------------------------------------------------

    def recent_events(self, limit: int | None = None) -> list[SessionEvent]:
        with self._events_lock:
            events = list(self._events)
        if limit is not None and limit >= 0:
            events = events[-limit:]
        return events

    def events_after(self, start_index: int) -> tuple[list[SessionEvent], int]:
        with self._events_lock:
            total = len(self._events)
            if start_index < 0:
                start_index = 0
            if start_index >= total:
                return ([], total)
            return (list(self._events[start_index:]), total)

    def poll_event(self, timeout: float = 0.0) -> SessionEvent | None:
        try:
            return self._event_queue.get(timeout=max(0.0, float(timeout)))
        except Empty:
            return None

    @property
    def terminal_event(self) -> SessionEvent | None:
        return self._terminal_event

    def stderr_snapshot(self) -> list[str]:
        with self._stderr_lock:
            return list(self._stderr_lines)

    # ----- internals ------------------------------------------------------

    def _default_command(self, *, prompt: str, resume_id: str) -> list[str]:
        """``codex exec [resume <id>] --json`` for one turn."""
        del prompt  # delivered on stdin, not as an argument
        command = [self._binary, 'exec']
        if resume_id:
            # A sub-subcommand, not a flag — and it accepts a RESTRICTED
            # option set: --sandbox / -C / --add-dir are rejected here because
            # the resumed turn inherits them from the original spawn.
            command.extend(['resume', resume_id])
        command.extend(['--json', '--skip-git-repo-check'])
        if self._model:
            command.extend(['-m', self._model])
        if not resume_id:
            command.extend(['--sandbox', self._sandbox_mode])
            if self._cwd:
                command.extend(['-C', self._cwd])
            for directory in self._additional_dirs:
                command.extend(['--add-dir', directory])
        return command

    def _record_user_message(self, message: str) -> None:
        """Put the operator's prompt into the event log.

        ``codex exec`` takes the prompt on STDIN and never echoes it back as
        an event, so the log held only the agent's output. The UI showed the
        prompt from a local bubble it appends on send — which a page reload
        discards, and the operator came back to a transcript containing
        answers to questions that were no longer there.

        Recorded in the persistent-process transport's ``user`` shape rather
        than a Codex-specific one: the chat renderer already knows how to
        draw that, and one wire shape for "the operator said this" is what
        lets a single UI tail either backend.
        """
        self._append_event({
            'type': 'user',
            'message': {'content': [{'type': 'text', 'text': message}]},
        })

    def _start_turn_locked(self, message: str) -> None:
        self._record_user_message(message)
        command = self._build_command(
            prompt=message, resume_id=self._agent_session_id,
        )
        self.logger.info(
            'task %s: starting a %s turn',
            self._task_id, 'resumed' if self._agent_session_id else 'fresh',
        )
        try:
            proc = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                errors='replace',
                cwd=self._cwd or None,
                env=self._build_env() if self._build_env else None,
            )
        except OSError as exc:
            self._record_stderr(f'failed to launch {self._binary}: {exc}')
            self._append_event({
                'type': CODEX_EVENT_TURN_ABORTED,
                'error': f'failed to launch {self._binary}: {exc}',
            })
            return
        self._proc = proc
        self._turn_thread = threading.Thread(
            target=self._run_turn, args=(proc, message), daemon=True,
            name=f'codex-turn-{self._task_id}',
        )
        self._turn_thread.start()

    def _run_turn(self, proc: subprocess.Popen, message: str) -> None:
        """Feed the prompt, stream events until the process exits."""
        try:
            if proc.stdin is not None:
                try:
                    proc.stdin.write(message)
                    proc.stdin.close()
                except (BrokenPipeError, OSError) as exc:
                    self._record_stderr(f'stdin write failed: {exc}')
            stderr_thread = threading.Thread(
                target=self._drain_stderr, args=(proc,), daemon=True,
                name=f'codex-stderr-{self._task_id}',
            )
            stderr_thread.start()
            saw_terminal = False
            if proc.stdout is not None:
                for line in proc.stdout:
                    event = self._append_json_line(line)
                    if event is not None and event.is_terminal:
                        saw_terminal = True
            returncode = proc.wait()
            if not saw_terminal:
                # A turn that ends without ``turn.completed`` — a crash, a
                # kill, a non-zero exit. Synthesise a terminal event so the
                # UI's in-flight indicator clears instead of spinning forever.
                #
                # ``error`` carries the best available explanation rather
                # than leaving the reader with a bare event name: the CLI's
                # own ``error`` events first (those name the actual refusal),
                # then stderr. A terminal event with no reason on it is what
                # reached the operator as an unexplained "turn.aborted".
                stderr_tail = self.stderr_snapshot()[-5:]
                self._append_event({
                    'type': CODEX_EVENT_TURN_ABORTED,
                    'returncode': returncode,
                    'stderr': '\n'.join(stderr_tail),
                    'error': self._failure_reason(returncode, stderr_tail),
                })
            stderr_thread.join(timeout=1.0)
        finally:
            if self._proc is proc:
                self._proc = None
            self._drain_pending_messages()

    def _failure_reason(self, returncode: int, stderr_tail: list[str]) -> str:
        """The most useful one-line explanation for a turn that died.

        Preference order matters. The CLI reports a refused model or a bad
        credential as an ``error`` event on STDOUT and says nothing about it
        on stderr, so a stderr-only reason would report the unrelated
        shell-snapshot warnings the CLI logs on every run.
        """
        for event in reversed(self.recent_events(limit=50)):
            raw = event.raw if isinstance(event.raw, dict) else {}
            if raw.get('type') != 'error':
                continue
            message = normalized_text(str(raw.get('message', '') or ''))
            if message:
                return message
        for line in reversed(stderr_tail):
            text = normalized_text(line)
            if text and 'Reading prompt from stdin' not in text:
                return text
        return f'the {self._binary} process exited with code {returncode}'

    def _drain_pending_messages(self) -> None:
        """Send whatever arrived mid-turn, as one follow-up turn."""
        with self._turn_lock:
            if self._terminated or not self._pending_messages:
                return
            queued = self._pending_messages
            self._pending_messages = []
            # Joined rather than run one-per-turn: the operator typed them as
            # consecutive thoughts, and replaying them as separate turns would
            # bill (and confuse) each one on its own.
            self._start_turn_locked('\n\n'.join(queued))

    def _append_json_line(self, line: str) -> SessionEvent | None:
        text = str(line or '').strip()
        if not text:
            return None
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            # Not every stdout line is an event; a CLI banner is not an error.
            self._record_stderr(text)
            return None
        if not isinstance(raw, dict):
            return None
        return self._append_event(raw)

    def _append_event(self, raw: dict[str, Any]) -> SessionEvent:
        event = SessionEvent(raw=raw)
        if event.event_type == CODEX_EVENT_THREAD_STARTED:
            self._absorb_thread_id(raw)
        with self._events_lock:
            self._events.append(event)
            if len(self._events) > _MAX_RECENT_EVENTS:
                del self._events[:-_MAX_RECENT_EVENTS]
        if event.is_terminal:
            self._terminal_event = event
        self._event_queue.put(event)
        with self._events_changed:
            self._events_changed.notify_all()
        return event

    def _absorb_thread_id(self, raw: dict[str, Any]) -> None:
        """Learn this chat's identity from the first turn's opening event.

        Codex calls it ``thread_id``; every consumer calls it
        ``agent_session_id``. Recorded once and never replaced: a later turn
        reporting a different id would mean the resume silently started a new
        conversation, and overwriting would hide that from the operator.
        """
        thread_id = normalized_text(raw.get('thread_id', ''))
        if not thread_id or thread_id == self._agent_session_id:
            return
        if self._agent_session_id:
            self.logger.warning(
                'task %s: resumed chat %s reported a DIFFERENT id %s — the '
                'resume did not attach to the original conversation',
                self._task_id, self._agent_session_id, thread_id,
            )
            return
        self._agent_session_id = thread_id
        callback = self._session_id_correction_callback
        if callable(callback):
            try:
                callback(thread_id)
            except Exception:
                self.logger.exception(
                    'task %s: session-id correction callback raised',
                    self._task_id,
                )

    def _drain_stderr(self, proc: subprocess.Popen) -> None:
        if proc.stderr is None:
            return
        for line in proc.stderr:
            self._record_stderr(line)

    def _record_stderr(self, line: str) -> None:
        text = str(line or '').rstrip()
        if not text:
            return
        with self._stderr_lock:
            self._stderr_lines.append(text)
            if len(self._stderr_lines) > _MAX_STDERR_LINES:
                del self._stderr_lines[:-_MAX_STDERR_LINES]
