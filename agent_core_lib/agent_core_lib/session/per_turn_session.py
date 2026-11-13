"""An interactive chat built on a CLI that runs one process per message.

Codex (``codex exec``), Grok (``grok -p``) and OpenCode (``opencode run``) are
all ONE-SHOT: they take a prompt, stream JSON events while they work, and
exit. There is no persistent stdin to write a second message to. Continuity
comes from resuming the CLI's own session id, which starts a fresh process
that remembers the earlier conversation.

So a chat here is not "a subprocess with a long life" — it is a small state
machine that spawns one process PER TURN and stitches their event streams into
one log the UI can tail:

* the first turn starts fresh and learns the session id from the CLI's
  opening event; that id is this chat's identity from then on;
* every later turn resumes that id;
* between turns there is no process at all, and the chat is still alive.

``is_alive`` therefore means "this chat can take another message", not "a
process is running" — under a per-turn model the process reading would be
False between every turn, which would tell every caller the chat was dead.
See ``agent_provider_contracts.agent_session``.

What this model gives up: nothing can be sent MID-turn (there is no stdin to
write to), so an operator message that arrives while a turn runs is queued and
sent when it finishes. What it gains: a wedged CLI cannot block anything — the
process either finishes or is killed, and the next turn starts clean.

THE TRANSPORT supplies only what differs per CLI, and supplies all of it —
the hooks are abstract, never defaulted, so a transport that forgets one fails
on its first turn instead of quietly mis-launching a different CLI:

* ``_default_command`` — the argv for one turn (fresh or resumed);
* ``_translate_event`` — the CLI's own JSON event, in the shared vocabulary
  (``turn_events``). The UI draws nothing else;
* ``PROMPT_ON_STDIN`` — whether the prompt is written to stdin or carried in
  the argv the command hook builds.
"""

from __future__ import annotations

import json
import subprocess
import threading
from queue import Empty, Queue
from typing import Any

from agent_core_lib.agent_core_lib.helpers.command_floor import (
    git_access_change_text,
    normalized_git_access,
)
from agent_core_lib.agent_core_lib.helpers.logging_utils import configure_logger
from agent_core_lib.agent_core_lib.session.turn_events import (
    TURN_EVENT_ERROR,
    TURN_EVENT_SESSION_STARTED,
    TURN_EVENT_TURN_ABORTED,
    TurnEvent,
    turn_completed,
    user_message,
)
from utils_core_lib.utils_core_lib.text_utils import normalized_text

_MAX_RECENT_EVENTS = 5000
_MAX_STDERR_LINES = 200


class PerTurnCliSession(object):
    """A chat with a one-process-per-turn CLI: one continuous event log."""

    #: Operator-facing name ('Codex'). A sentinel default so a missing
    #: override shows up in the message instead of naming another vendor.
    DISPLAY_NAME = '<unnamed CLI>'
    #: The CLI binary used when the host passes none.
    DEFAULT_BINARY = ''
    #: The sandbox mode a fresh turn runs in when the host passes none
    #: ('' for a CLI that has no sandbox flag).
    DEFAULT_SANDBOX_MODE = ''
    #: True: the prompt is written to the turn's stdin. False: the command hook
    #: carries it in argv and stdin is closed. Deliberately unset here.
    PROMPT_ON_STDIN: bool | None = None
    #: stderr lines that are the CLI's own chatter on every run, never the
    #: reason a turn died.
    STDERR_NOISE: tuple[str, ...] = ()
    #: True for a CLI that prints NO terminal event on success (OpenCode): a
    #: clean exit then completes the turn. Every other CLI's missing terminal
    #: event means the turn was cut short.
    CLEAN_EXIT_ENDS_TURN = False

    def __init__(
        self,
        *,
        task_id: str,
        cwd: str = '',
        binary: str = '',
        model: str = '',
        agent_session_id: str = '',
        additional_dirs: tuple[str, ...] | list[str] = (),
        sandbox_mode: str = '',
        build_command=None,
        build_env=None,
        logger=None,
        git_access_source=None,
    ) -> None:
        self._task_id = str(task_id or '')
        self._cwd = str(cwd or '')
        self._binary = str(binary or self.DEFAULT_BINARY)
        self._model = str(model or '')
        # Non-empty when resuming an existing conversation — a chat the
        # operator returned to, or one rehydrated after a host restart.
        self._agent_session_id = normalized_text(agent_session_id)
        self._additional_dirs = tuple(str(d) for d in (additional_dirs or ()) if d)
        self._sandbox_mode = str(sandbox_mode or self.DEFAULT_SANDBOX_MODE)
        self._build_command = build_command or self._default_command
        self._build_env = build_env
        self.logger = logger or configure_logger(self.__class__.__name__)
        # Git access (``command_floor``) is asked for at the START OF EVERY
        # TURN — a turn is its own process, so a changed setting applies to the
        # next message with no respawn. ``None`` = strict. ``_told_git_access``
        # is what the agent was last told (None = never), so the rules are
        # repeated only when the level changes.
        self._git_access_source = git_access_source
        self._turn_git_access = ''
        self._told_git_access: str | None = None

        self._events: list[TurnEvent] = []
        self._events_lock = threading.Lock()
        self._event_queue: Queue = Queue()
        self._events_changed = threading.Condition()
        self._stderr_lines: list[str] = []
        self._stderr_lock = threading.Lock()

        self._proc: subprocess.Popen | None = None
        self._turn_lock = threading.Lock()
        self._terminated = False
        self._turn_thread: threading.Thread | None = None
        self._terminal_event: TurnEvent | None = None
        # Messages that arrived mid-turn. There is no stdin to interrupt, so
        # they wait rather than being dropped — dropping an operator's message
        # is the one outcome worse than a delay.
        self._pending_messages: list[str] = []
        self._session_id_correction_callback = None

    # ----- hooks the concrete transport MUST provide ---------------------

    def _default_command(self, *, prompt: str, resume_id: str) -> list[str]:
        """The argv for one turn; ``resume_id`` is '' for a fresh one."""
        raise NotImplementedError('transport must implement _default_command')

    def _translate_event(self, raw: dict[str, Any]) -> list[dict[str, Any]]:
        """The CLI's own event, as zero or more events in the shared vocabulary."""
        raise NotImplementedError('transport must implement _translate_event')

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
        """Can this chat still take a message? Only ``terminate`` ends it."""
        return not self._terminated

    @property
    def is_working(self) -> bool:
        """True while a turn's process is running."""
        proc = self._proc
        return proc is not None and proc.poll() is None

    def allowed_additional_dirs(self) -> list[str]:
        return list(self._additional_dirs)

    @property
    def git_access(self) -> str:
        """The git access level of the running (or last) turn.

        Between turns, the level the NEXT turn would get. The host reads this
        to know whether the agent may be running git in its clones right now.
        """
        if self.is_working and self._turn_git_access:
            return self._turn_git_access
        return self._current_git_access()

    def _current_git_access(self) -> str:
        source = self._git_access_source
        if not callable(source):
            return normalized_git_access('')
        try:
            return normalized_git_access(source())
        except Exception:  # noqa: BLE001 - an unreadable setting means strict
            return normalized_git_access('')

    # ----- lifecycle ------------------------------------------------------

    def start(self, initial_prompt: str = '') -> None:
        """Bring the chat up. A turn runs only if there is something to say."""
        self._terminated = False
        prompt = str(initial_prompt or '').strip()
        if prompt:
            self.send_user_message(prompt)

    def send_user_message(self, text: str, **kwargs: Any) -> None:
        """Run one turn with ``text``. Returns as soon as it is underway.

        Mid-turn messages queue: there is no stdin to interrupt, so the
        alternative would be dropping what the operator typed.
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

    def recent_events(self, limit: int | None = None) -> list[TurnEvent]:
        with self._events_lock:
            events = list(self._events)
        if limit is not None and limit >= 0:
            # ``events[-0:]`` is the WHOLE list, so a limit of 0 needs its own case.
            events = events[-limit:] if limit else []
        return events

    def events_after(self, start_index: int) -> tuple[list[TurnEvent], int]:
        with self._events_lock:
            total = len(self._events)
            if start_index < 0:
                start_index = 0
            if start_index >= total:
                return ([], total)
            return (list(self._events[start_index:]), total)

    def poll_event(self, timeout: float = 0.0) -> TurnEvent | None:
        try:
            return self._event_queue.get(timeout=max(0.0, float(timeout)))
        except Empty:
            return None

    @property
    def terminal_event(self) -> TurnEvent | None:
        return self._terminal_event

    def stderr_snapshot(self) -> list[str]:
        with self._stderr_lock:
            return list(self._stderr_lines)

    # ----- internals ------------------------------------------------------

    def _record_user_message(self, message: str) -> None:
        """Put the operator's prompt into the event log.

        A per-turn CLI takes the prompt as input and never echoes it back as an
        event, so the log would hold only the agent's output — and a page
        reload would bring the operator back to answers to questions that are
        no longer there.
        """
        self._append_event(user_message(message))

    def _start_turn_locked(self, message: str) -> None:
        if self.PROMPT_ON_STDIN is None:
            raise NotImplementedError('transport must set PROMPT_ON_STDIN')
        # The transcript keeps what the operator typed; the git rules, when the
        # level changed since the agent was last told, travel in front of it.
        self._record_user_message(message)
        self._turn_git_access = self._current_git_access()
        notice = git_access_change_text(self._turn_git_access, self._told_git_access)
        self._told_git_access = self._turn_git_access
        delivered = f'{notice}\n{message}' if notice else message
        command = self._build_command(
            prompt=delivered, resume_id=self._agent_session_id,
        )
        self.logger.info(
            'task %s: starting a %s %s turn',
            self._task_id,
            'resumed' if self._agent_session_id else 'fresh',
            self.DISPLAY_NAME,
        )
        try:
            proc = subprocess.Popen(
                command,
                stdin=subprocess.PIPE if self.PROMPT_ON_STDIN else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                errors='replace',
                cwd=self._cwd or None,
                env=self._turn_env(),
                # Own process group, so anything the agent starts (an
                # ``npm run dev``, a watcher) dies with the session instead of
                # outliving it.
                start_new_session=True,
            )
        except OSError as exc:
            self._record_stderr(f'failed to launch {self._binary}: {exc}')
            self._append_event({
                'type': TURN_EVENT_TURN_ABORTED,
                'error': f'failed to launch {self._binary}: {exc}',
            })
            return
        self._proc = proc
        self._turn_thread = threading.Thread(
            target=self._run_turn, args=(proc, delivered), daemon=True,
            name=f'{self.DISPLAY_NAME.lower()}-turn-{self._task_id}',
        )
        self._turn_thread.start()

    def _run_turn(self, proc: subprocess.Popen, message: str) -> None:
        """Feed the prompt (when it goes on stdin), stream events until exit."""
        try:
            if self.PROMPT_ON_STDIN and proc.stdin is not None:
                try:
                    proc.stdin.write(message)
                    proc.stdin.close()
                except (BrokenPipeError, OSError) as exc:
                    self._record_stderr(f'stdin write failed: {exc}')
            stderr_thread = threading.Thread(
                target=self._drain_stderr, args=(proc,), daemon=True,
                name=f'{self.DISPLAY_NAME.lower()}-stderr-{self._task_id}',
            )
            stderr_thread.start()
            saw_terminal = False
            if proc.stdout is not None:
                for line in proc.stdout:
                    if any(event.is_terminal for event in self._append_json_line(line)):
                        saw_terminal = True
            returncode = proc.wait()
            if not saw_terminal and returncode == 0 and self.CLEAN_EXIT_ENDS_TURN:
                self._append_event(turn_completed())
            elif not saw_terminal:
                # A turn that ends without a terminal event — a crash, a kill, a
                # non-zero exit. Synthesise one so the UI's in-flight indicator
                # clears, carrying the best explanation available rather than a
                # bare event name.
                stderr_tail = self.stderr_snapshot()[-5:]
                self._append_event({
                    'type': TURN_EVENT_TURN_ABORTED,
                    'returncode': returncode,
                    'stderr': '\n'.join(stderr_tail),
                    'error': self._failure_reason(returncode, stderr_tail),
                })
            stderr_thread.join(timeout=1.0)
        finally:
            if self._proc is proc:
                self._proc = None
            self._drain_pending_messages()

    def _turn_env(self) -> dict[str, str] | None:
        """The environment one turn runs with (``None`` inherits the host's).

        A transport whose CLI takes settings from the environment for each
        turn (a working directory, an inline config) extends this.
        """
        return self._build_env() if self._build_env else None

    def _failure_reason(self, returncode: int, stderr_tail: list[str]) -> str:
        """The most useful one-line explanation for a turn that died.

        The CLI's own ``error`` events come first: a refused model or a bad
        credential is reported on STDOUT, while stderr carries whatever the CLI
        logs on every run.
        """
        for event in reversed(self.recent_events(limit=50)):
            raw = event.raw if isinstance(event.raw, dict) else {}
            if raw.get('type') != TURN_EVENT_ERROR:
                continue
            message = normalized_text(str(raw.get('message', '') or ''))
            if message:
                return message
        for line in reversed(stderr_tail):
            text = normalized_text(line)
            if text and not any(noise in text for noise in self.STDERR_NOISE):
                return text
        return f'the {self._binary} process exited with code {returncode}'

    def _drain_pending_messages(self) -> None:
        """Send whatever arrived mid-turn, as one follow-up turn."""
        with self._turn_lock:
            if self._terminated or not self._pending_messages:
                return
            queued = self._pending_messages
            self._pending_messages = []
            # Joined rather than run one per turn: the operator typed them as
            # consecutive thoughts, and separate turns would bill (and confuse)
            # each one on its own.
            self._start_turn_locked('\n\n'.join(queued))

    def _append_json_line(self, line: str) -> list[TurnEvent]:
        text = str(line or '').strip()
        if not text:
            return []
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            # Not every stdout line is an event; a CLI banner is not an error.
            self._record_stderr(text)
            return []
        if not isinstance(raw, dict):
            return []
        return [self._append_event(event) for event in self._translate_event(raw)]

    def _append_event(self, raw: dict[str, Any]) -> TurnEvent:
        event = TurnEvent(raw=raw)
        if event.event_type == TURN_EVENT_SESSION_STARTED:
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

        Recorded once and never replaced: a later turn reporting a different id
        would mean the resume silently started a new conversation, and
        overwriting would hide that from the operator.
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
