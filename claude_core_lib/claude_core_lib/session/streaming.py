"""Long-lived `claude -p` stream-json subprocess wrapper.

Unlike :class:`the orchestrator.client.claude.cli_client.ClaudeCliClient` (one-shot,
single prompt → single result), this wrapper keeps the Claude CLI process
alive for the duration of a planning conversation: events stream out as
NDJSON, follow-up user messages stream in. :class:`ClaudeSessionManager`
maps each session 1-to-1 with an orchestrator task so a human can chat with Claude
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

from agent_core_lib.agent_core_lib.helpers.session_id_utils import (
    AGENT_SESSION_ID,
    fix_session_id,
)
from claude_core_lib.claude_core_lib.session.wire_protocol import (
    CLAUDE_EVENT_ASSISTANT,
    CLAUDE_EVENT_CONTROL_REQUEST,
    CLAUDE_EVENT_CONTROL_RESPONSE,
    CLAUDE_EVENT_PERMISSION_RESPONSE,
    CLAUDE_EVENT_RESULT,
    CLAUDE_EVENT_SYSTEM,
    CLAUDE_SYSTEM_SUBTYPE_INIT,
    CLAUDE_SYSTEM_SUBTYPE_SANDBOX_WARNING,
    PERMISSION_REQUEST_EVENT_TYPES,
)
from agent_core_lib.agent_core_lib.helpers.credential_scan import (
    scan_text_for_credentials_and_phishing,
)
from claude_core_lib.claude_core_lib.helpers.spawn_utils import (
    append_additional_dirs,
    append_model_effort_flags,
    build_appended_system_prompt,
    build_claude_subprocess_env,
    wrap_spawn_for_docker,
)
from agent_core_lib.agent_core_lib.helpers.command_introspection import (
    classify_command_escape,
)
from claude_core_lib.claude_core_lib.helpers.sandbox_scope import (
    classify_command_sandbox,
    classify_tool_input_sandbox,
)
from claude_core_lib.claude_core_lib.helpers.context_window import (
    context_window_tokens,
    prompt_tokens_from_usage,
    resolved_model_of_event,
    usage_of_event,
    widen_window_to_observed,
)
from claude_core_lib.claude_core_lib.session.index import parse_jsonl_dict_line
from claude_core_lib.claude_core_lib.session.registry import kill_process_tree
from agent_core_lib.agent_core_lib.helpers.cli_shim_utils import (
    resolve_windows_cli_invocation,
)
from agent_core_lib.agent_core_lib.helpers.logging_utils import configure_logger
from utils_core_lib.utils_core_lib.text_utils import (
    condensed_text,
    normalized_text,
    text_from_mapping,
)


_IS_WINDOWS = os.name == 'nt'


def _wait_for_exit(proc: subprocess.Popen, timeout: float) -> bool:
    """Block up to ``timeout`` seconds for ``proc`` to exit. True on exit."""
    try:
        proc.wait(timeout=timeout)
        return True
    except subprocess.TimeoutExpired:
        return False


# How long a sent-but-unanswered user message reads as "working" before
# ``is_working`` lets it fall back to idle. Covers the warm-up window where
# ``send_user_message`` has written to stdin but Claude has not yet emitted
# its first event (we don't pass --include-partial-messages, so NOTHING marks
# that window on the wire). Without it a BACKGROUNDED tab (polled
# ``is_working``) reads "idle" while the FOCUSED tab (live ``turnInFlight``,
# set on send) reads "working" — the focus-dependent status-dot bug.
#
# This is the SAME budget the orchestrator's comment dispatch uses to age out a stalled
# session (``_COMMENT_SEND_ACK_GRACE_SECONDS`` imports this very value), and
# they MUST stay equal: ``_task_session_is_stalled`` requires ``is_working``
# to have already flipped False by the time it ages a stall out, so the
# warm-up grace here may not exceed the dispatch grace there.
TURN_ACK_GRACE_SECONDS = 60.0


# Hard caps on attached images. Anthropic's API allows up to 20 images
# per request and ~5MB per image; the orchestrator is more conservative because a
# misclick on a 4K screenshot can blow up the prompt and the per-task
# token bill. Operator can paste up to 10 screenshots per message.
_MAX_IMAGES_PER_MESSAGE = 10
_MAX_IMAGE_BYTES = 5 * 1024 * 1024
_ALLOWED_IMAGE_MEDIA_TYPES = frozenset({
    'image/png',
    'image/jpeg',
    'image/gif',
    'image/webp',
})


def _validate_image_blocks(images) -> list[dict]:
    """Coerce a list of ``{media_type, data}`` dicts into Anthropic image blocks.

    Bad entries are dropped silently rather than raising — a single
    corrupt paste shouldn't block the whole message. Quietly capping
    at ``_MAX_IMAGES_PER_MESSAGE`` for the same reason.
    """
    if not isinstance(images, list):
        return []
    blocks: list[dict] = []
    for entry in images[:_MAX_IMAGES_PER_MESSAGE]:
        if not isinstance(entry, dict):
            continue
        media_type = text_from_mapping(entry, 'media_type').lower()
        if media_type not in _ALLOWED_IMAGE_MEDIA_TYPES:
            continue
        data = text_from_mapping(entry, 'data')
        if not data:
            continue
        # Base64 expansion is ~4/3 of the raw byte count. Reject
        # anything past the cap up-front so we don't write a huge
        # envelope down the agent's stdin.
        if len(data) > int(_MAX_IMAGE_BYTES * 4 / 3) + 1024:
            continue
        blocks.append({
            'type': 'image',
            'source': {
                'type': 'base64',
                'media_type': media_type,
                'data': data,
            },
        })
    return blocks


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
        lessons_path: str = '',
        docker_mode_on: bool = False,
        sandbox_root: str = '',
        additional_dirs: list[str] | None = None,
        done_callback=None,
        done_sentinel: str = '',
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
        else:
            # ALWAYS route asks over stdio — INCLUDING bypassPermissions mode.
            # bypassPermissions auto-approves regular TOOL permissions without
            # ever invoking the prompt tool, so keeping it set is free for
            # normal tool use. What it DOES buy is a channel for the agent's
            # ``AskUserQuestion`` (a question to the human, NOT a tool
            # permission): without a prompt tool the headless CLI auto-answers
            # the question itself and the agent barrels on. The rule is that
            # EVERY question the agent asks is answered by a human — never
            # auto-answered — so the ask must always come back to the host,
            # regardless of permission mode. (The rare bypass-mode circuit
            # breakers — rm -rf ~, explicit ask-rules — route here too, which
            # is also what we want.)
            self._permission_prompt_tool = self.DEFAULT_PERMISSION_PROMPT_TOOL
        self._allowed_tools = normalized_text(allowed_tools)
        self._disallowed_tools = normalized_text(disallowed_tools)
        self._max_turns = max_turns
        # Route through the one-shot client's coercion so streaming and
        # one-shot validate ``--effort`` against the same level set
        # (derived from ``effort_levels.FALLBACK_EFFORT_LEVELS``) and a
        # typo fails at spawn instead of mid-turn.
        from claude_core_lib.claude_core_lib.cli_client import ClaudeCliClient as _CliClient
        self._effort = _CliClient._coerce_effort(effort)
        self._resume_session_id = fix_session_id(resume_session_id)
        # One-shot guards so the session-id verification lines (see
        # ``_maybe_capture_session_id``) print exactly once per spawn:
        # one INFO confirming the id Claude actually ran with, or one
        # WARNING if it differs from the id the orchestrator pinned / asked to
        # resume (which is what "the conversation restarted fresh"
        # looks like from the operator's side).
        self._session_id_confirmed = False
        self._session_id_mismatch_logged = False
        # Resume-verdict flags, set from the CLI's init event. The
        # session manager polls these right after a ``--resume`` spawn:
        # ``_resume_confirmed`` means the CLI echoed the requested id
        # (history actually loaded); ``_resume_ignored`` means the CLI
        # announced a DIFFERENT id — it silently started a fresh,
        # memoryless session instead of resuming (observed on Windows
        # when a leftover CLI process still held the transcript). An
        # ignored resume must be treated as a FAILED spawn, never as a
        # live conversation.
        self._resume_confirmed = False
        self._resume_ignored = False
        # Optional callback: ``fn(actual_session_id)`` fired when Claude
        # announces its actual session id via the init event and it differs
        # from what the orchestrator expected. The manager registers this to keep its
        # persisted record in sync so the next ``--resume`` uses the right id.
        self._session_id_correction_callback = None
        self._architecture_doc_path = normalized_text(architecture_doc_path)
        self._lessons_path = normalized_text(lessons_path)
        # Configured product files the agent is MEANT to touch (the orchestrator writes
        # learned lessons here and reads the architecture doc), even though
        # they live outside the task folder. Allow-listed so the
        # out-of-sandbox warning never fires on them. See sandbox_scope.
        self._sandbox_allowed_paths = tuple(
            p for p in (self._architecture_doc_path, self._lessons_path) if p
        )
        # Extra directories Claude is allowed to read/edit beyond
        # ``cwd``. For multi-repo tasks the chat path uses this to
        # surface sibling repo clones (e.g. all task repos under
        # ``~/.the orchestrator/workspaces/<task>/``); without it Claude only
        # sees the cwd and refuses cross-repo questions like
        # "verify the front end" with a "forbidden repository"
        # response when the only frontend-named entry it knows about
        # came from ``the ignored-folders setting``.
        self._additional_dirs = [
            normalized_text(str(d)) for d in (additional_dirs or [])
            if d is not None and normalized_text(str(d))
        ]
        # Set from ``the docker setting`` at boot, threaded down through
        # the session manager. Independent of ``permission_mode``: docker
        # is the *containment* layer (sandbox), permission_mode is the
        # *prompt* layer (acceptEdits vs bypassPermissions).
        self._docker_mode_on = bool(docker_mode_on)
        # The directory the docker sandbox bind-mounts, when it should be
        # something WIDER than ``cwd``. ``cwd`` is one repository clone, so
        # mounting it makes every OTHER repo in the same task invisible inside
        # the container — a multi-repo task loses cross-repo access entirely.
        # The caller passes the task folder here (the parent holding every
        # clone for this task), which is both the boundary an operator means
        # by "never leave the task folder" and the smallest mount that keeps
        # multi-repo work possible. Empty ⇒ fall back to ``cwd`` (the previous
        # behaviour) so a caller that cannot prove a task folder never widens
        # the mount by accident.
        self._sandbox_root = normalized_text(sandbox_root)
        self._env_overrides = dict(env or {})
        # Callback fired once when an assistant message arrives that
        # contains the done-sentinel token. Wired by the session manager to
        # the host's done-callback so the agent can end the chat by emitting
        # the magic string. The sentinel string is supplied by the host (the
        # lib ships a generic default) so this stays product-agnostic.
        # ``_done_sentinel_fired`` guards against re-firing on later
        # messages that quote the sentinel back.
        self._done_callback = done_callback
        self._done_sentinel = normalized_text(done_sentinel) or '<AGENT_TASK_DONE>'
        self._done_sentinel_fired = False

        self._proc: subprocess.Popen[bytes] | None = None
        # Set at spawn time when docker_mode_on — lets the kill-escalation
        # path issue a direct `docker kill` if the wrapping `docker run`
        # client process itself has to be force-killed (see _escalate_to_kill).
        self._docker_container_name: str = ''
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
        # Out-of-task-folder write paths already warned about this session,
        # so a repeated edit to the same external file doesn't spam the
        # chat. Touched only from the single stdout-reader thread.
        self._sandbox_warned_paths: set[str] = set()
        # Notified every time an event is appended OR the session
        # terminates. SSE consumers wait on it instead of busy-polling
        # ``recent_events()`` every 100ms — that polling pattern was
        # the dominant source of streamed-event latency AND it copied
        # the full event list on every tick (O(N) per poll on a long
        # session). The condition is paired with the existing
        # ``_recent_events_lock`` so callers can atomically (a) take
        # a snapshot of new entries and (b) record the new high-water
        # index without a TOCTOU window.
        self._events_changed = threading.Condition(self._recent_events_lock)
        # Latest turn's prompt size, for the context-window indicator. Its
        # own lock: written from the stdout reader thread, read from the
        # webserver thread, and it must never contend with event delivery.
        self._context_usage_lock = threading.Lock()
        self._context_used_tokens = 0
        # The model the CLI actually resolved the alias to, learned from the
        # stream. Only this can size the context window (see context_usage).
        self._resolved_model = ''
        self._agent_session_id: str = ''
        self._terminal_event: SessionEvent | None = None
        self._reader_threads: list[threading.Thread] = []
        self._stderr_lines: list[str] = []
        self._stderr_lock = threading.Lock()
        # Count of user messages forwarded to the CLI subprocess. Paired
        # with ``result_events_received`` to detect "in-flight messages
        # whose turn hasn't started yet" — there is a real race window
        # where ``send_user_message`` has written to stdin but Claude
        # has not yet emitted its first event, so ``is_working`` (which
        # walks ``_recent_events``) still returns False. Comment
        # dispatch used to slip into that gap, fire its own
        # ``send_user_message`` on a "false-idle" session, and then get
        # marked ``ADDRESSED`` the moment the PRIOR turn's RESULT fired
        # — well before the comment's own turn had even started.
        # ``AgentService._task_has_busy_turn`` now also requires
        # ``user_messages_sent <= result_events_received`` to call a
        # session idle.
        self._user_messages_sent = 0
        # Wall-clock of the most recent ``send_user_message``. Lets
        # callers tell a genuine in-flight turn (message just sent,
        # Claude about to respond) apart from a STALLED session (a
        # message sent long ago that never produced a ``result`` —
        # the subprocess is alive but no longer processing stdin).
        # Without this, ``user_messages_sent > result_events_received``
        # stays true forever on a stalled session and blocks all
        # comment dispatch. Updated under ``_user_messages_sent_lock``.
        self._last_user_message_sent_epoch = 0.0
        self._user_messages_sent_lock = threading.Lock()
        self.logger = configure_logger(self.__class__.__name__)
        if self._permission_mode == 'bypassPermissions':
            self.logger.warning(
                'the bypass-permissions setting=true: streaming Claude session '
                'for task %s will run with --permission-mode bypassPermissions. '
                'The planning UI will not intercept tool calls — the agent can '
                'run Bash, Edit, Write, and any other tool without asking. '
                '(AskUserQuestion is still routed to the operator — questions '
                'are never auto-answered.) The operator who set this flag '
                'accepts responsibility for any harm caused by the agent. See '
                'SECURITY.md.',
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
    def agent_session_id(self) -> str:
        return self._agent_session_id

    @property
    def resume_confirmed(self) -> bool:
        """True once the CLI's init event echoed the requested ``--resume`` id."""
        return self._resume_confirmed

    @property
    def resume_was_ignored(self) -> bool:
        """True when this spawn asked for ``--resume <id>`` but the CLI
        announced a DIFFERENT session id — i.e. it silently started a
        fresh conversation with no memory of the task. The manager
        treats this as a failed spawn and terminates the impostor."""
        return self._resume_ignored

    def allowed_additional_dirs(self) -> tuple[str, ...]:
        """Spawn-time ``--add-dir`` paths the live subprocess was given.

        The Claude CLI bakes its sandbox into the subprocess at spawn
        time — there's no in-flight widening API. Operators who clone
        new repos for the task after the chat tab is already open
        need to restart the tab to pick them up. Callers
        (``AgentService.sync_task_repositories``) compare the new
        clone paths against this set to surface a
        ``requires_session_restart`` signal in the sync response.
        """
        return tuple(self._additional_dirs)

    @property
    def sandbox_allowed_paths(self) -> tuple[str, ...]:
        """Specific files the agent may touch even outside the task folder
        (e.g. the orchestrator's lessons / architecture docs). Exposed so a caller that
        re-classifies a tool input (the webserver Action Guard) applies the
        same allow-list the live sandbox annotation does."""
        return tuple(self._sandbox_allowed_paths)

    def pending_request_input(self, request_id: str) -> tuple[str, dict]:
        """Return ``(tool_name, tool_input)`` for a still-pending control
        request, read from the SERVER-SIDE captured dict — so a caller never
        has to trust a (tamperable) client-supplied command. ``('', {})``
        when the id is unknown / already answered."""
        with self._pending_control_requests_lock:
            request = self._pending_control_requests.get(
                str(request_id or '').strip(), {},
            )
        if not isinstance(request, dict):
            return '', {}
        tool_name = str(
            request.get('tool_name') or request.get('tool') or '',
        ).strip()
        tool_input = request.get('input')
        return tool_name, (tool_input if isinstance(tool_input, dict) else {})

    @property
    def is_alive(self) -> bool:
        with self._proc_lock:
            return self._proc is not None and self._proc.poll() is None

    @property
    def is_working(self) -> bool:
        """True when Claude is mid-turn — a message is in flight.

        Mirrors the planning UI's ``turnInFlight`` reducer, so the tab dot
        is identical whether the tab is FOCUSED (live SSE ``turnInFlight``)
        or BACKGROUNDED (this property, polled every 5s). When the two
        disagree the dot flips on focus — the focus-dependent status bug.

        The reducer flips ``turnInFlight`` true the instant the operator
        SENDS a message, long before any event comes back. This property
        must do the same, and the event log alone cannot: we deliberately
        omit --include-partial-messages, so during the multi-second warm-up
        between ``send_user_message`` (stdin) and Claude's first
        ``assistant`` event there is NOTHING on the wire — the newest
        logged event is the PRIOR turn's ``result``, which reads "idle".

        Two signals, in order:

        1. A sent-but-unanswered message inside ``TURN_ACK_GRACE_SECONDS``
           (``user_messages_sent > result_events_received``) — the warm-up
           window AND the whole turn that follows. Bounded by the grace so
           a STALLED subprocess (alive, stopped reading stdin) ages back to
           idle instead of sticking "working" forever; ``is_alive`` gates it
           too, so a dead subprocess never reads working.
        2. Otherwise the latest turn has closed — walk the log for the
           background-WAIT case (a Monitor / run_in_background the turn
           scheduled and is still blocked on counts as working until a
           newer turn supersedes it).
        """
        if not self.is_alive:
            return False
        if self._has_unacked_turn_within_grace():
            return True
        with self._recent_events_lock:
            events = list(self._recent_events)
        for index in range(len(events) - 1, -1, -1):
            event = events[index]
            event_type = event.event_type
            if event_type == CLAUDE_EVENT_RESULT:
                # Turn closed. If it scheduled a background WAIT (a Monitor
                # / run_in_background tool) the agent is still effectively
                # working — waiting on that result — so keep it "working"
                # until a newer turn supersedes it. Otherwise it's idle.
                return self._turn_scheduled_background_wait(events, index)
            if event_type in ('assistant', 'stream_event', 'user'):
                return True
            if (
                event_type == CLAUDE_EVENT_SYSTEM
                and event.subtype == CLAUDE_SYSTEM_SUBTYPE_INIT
            ):
                return True
        return False

    def _has_unacked_turn_within_grace(self) -> bool:
        """True when a forwarded user message has no ``result`` yet AND the
        send is recent (< ``TURN_ACK_GRACE_SECONDS``).

        This is the inverse of the stalled-session condition: a fresh
        unacked turn is "working" (it just hasn't reached the wire); an
        aged unacked turn is a stall, so this returns False and lets the
        caller fall back to idle (the orchestrator's dispatch path then requeues it)."""
        if self.user_messages_sent <= self.result_events_received:
            return False
        last_sent = self.last_user_message_sent_epoch
        if last_sent <= 0:
            return False
        return (time.time() - last_sent) < TURN_ACK_GRACE_SECONDS

    # Tools that park the agent on a long-running wait (it scheduled the
    # work and is blocked on its result). Treated as "still working" even
    # after the turn closes, so a 10-minute test/build wait doesn't read
    # as idle. ``run_in_background`` on any tool counts too.
    #
    # ``Workflow`` returns immediately but runs in the BACKGROUND and emits a
    # ``<task-notification>`` (a fresh turn) when it completes — so the turn
    # that launched it has not really finished. Without it here the launching
    # turn's ``result`` reads "idle", the session looks done, and the host
    # tears it down before the workflow can notify back into the chat.
    _BACKGROUND_WAIT_TOOLS = frozenset({'Monitor', 'Workflow'})

    def _turn_scheduled_background_wait(self, events, result_index: int) -> bool:
        """True if the turn ending at ``result_index`` left a background
        wait outstanding (its last actions include a Monitor / background
        tool). Scans backward over that one turn only (stops at the prior
        ``result``)."""
        for j in range(result_index - 1, -1, -1):
            if events[j].event_type == CLAUDE_EVENT_RESULT:
                return False
            if self._event_has_background_wait_tool(events[j]):
                return True
        return False

    def _event_has_background_wait_tool(self, event: SessionEvent) -> bool:
        raw = event.raw if isinstance(event.raw, dict) else {}
        message = raw.get('message')
        content = message.get('content') if isinstance(message, dict) else None
        if not isinstance(content, list):
            return False
        for block in content:
            if not isinstance(block, dict) or block.get('type') != 'tool_use':
                continue
            if str(block.get('name') or '') in self._BACKGROUND_WAIT_TOOLS:
                return True
            tool_input = block.get('input')
            if isinstance(tool_input, dict) and tool_input.get('run_in_background') is True:
                return True
        return False

    @property
    def user_messages_sent(self) -> int:
        """Count of user messages forwarded to the CLI since spawn.

        Paired with ``result_events_received`` by callers that need to
        tell "session is mid-turn" apart from "session has in-flight
        messages whose turn hasn't started yet". See the counter init
        in ``__init__`` for the race that motivates this.
        """
        with self._user_messages_sent_lock:
            return self._user_messages_sent

    @property
    def effort(self) -> str:
        """The ``--effort`` level this subprocess was spawned with ('' = none).

        Read by callers (the chat send route) to decide whether a
        requested effort change needs a respawn — the CLI bakes
        ``--effort`` at spawn time, so it can't change mid-session.
        """
        return self._effort

    @property
    def model(self) -> str:
        """The ``--model`` value this subprocess was spawned with ('' = none).

        Same shape as ``effort`` — the CLI bakes ``--model`` at spawn
        time, so an operator-changed model only takes effect when the
        subprocess respawns. The chat send route reads this to decide
        whether the new picker value differs from the live session and
        therefore requires a respawn (instead of forwarding the message
        into a subprocess that's already wired to a model the operator
        no longer wants).
        """
        return self._model

    @property
    def permission_mode(self) -> str:
        """The ``--permission-mode`` this subprocess was spawned with.

        Same shape as ``effort`` / ``model`` — the CLI bakes
        ``--permission-mode`` at spawn time, so an operator toggling the
        composer's plan-mode lock only takes effect on a fresh
        subprocess. The chat send route reads this to decide whether the
        requested mode differs from the live session and therefore needs
        a respawn (instead of forwarding a message into a subprocess that
        is still wired to the old mode — e.g. one that can still edit
        when the operator has since locked it to planning-only).
        """
        return self._permission_mode

    def _sandbox_mount(self) -> tuple[str, str]:
        """``(bind_mount_root, workdir_subpath)`` for the docker sandbox.

        Without a ``sandbox_root`` this is the old behaviour: mount ``cwd``,
        WORKDIR at the mount root. With one, mount the task folder so every
        repo in the task is reachable, and keep the agent's working directory
        on the SAME repo it would have had — widening the mount must not
        silently relocate the agent.

        Falls back to mounting ``cwd`` if ``cwd`` is not actually inside
        ``sandbox_root``; a mount root that doesn't contain the working
        directory would put the agent outside its own sandbox.
        """
        if not self._sandbox_root:
            return self._cwd, ''
        root = os.path.normpath(self._sandbox_root)
        cwd = os.path.normpath(self._cwd) if self._cwd else ''
        if not cwd or cwd == root:
            return root, ''
        try:
            relative = os.path.relpath(cwd, root)
        except ValueError:
            # Different drives on Windows — no containment relationship.
            return self._cwd, ''
        if relative.startswith(os.pardir) or os.path.isabs(relative):
            return self._cwd, ''
        return root, relative.replace(os.sep, '/')

    @property
    def disallowed_tools(self) -> str:
        """The ``--disallowed-tools`` CSV this subprocess was spawned with.

        Baked at spawn time like ``permission_mode``, and exposed for the same
        reason: a caller comparing "what the operator now wants" against "what
        is actually running" needs BOTH halves. A read-only turn can be
        expressed by tool denial rather than by permission mode, and a
        comparison that looked only at ``permission_mode`` would call such a
        session unchanged and forward a message into a subprocess still wired
        to the previous restriction.
        """
        return self._disallowed_tools

    @property
    def last_user_message_sent_epoch(self) -> float:
        """Wall-clock of the most recent ``send_user_message``.

        ``0.0`` when no user message has been forwarded yet. Paired
        with ``user_messages_sent``/``result_events_received`` so a
        caller can age out a stalled in-flight message (sent long ago,
        never answered) instead of treating the session as busy
        forever.
        """
        with self._user_messages_sent_lock:
            return self._last_user_message_sent_epoch

    @property
    def result_events_received(self) -> int:
        """Count of ``result`` events received since spawn.

        Walks the event log instead of a separate counter because the
        log is the source of truth (e.g. a recovered session
        replays its NDJSON history into ``_recent_events`` directly).
        """
        with self._recent_events_lock:
            return sum(
                1 for e in self._recent_events
                if e.event_type == CLAUDE_EVENT_RESULT
            )

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
            # Docker mode wraps the spawn in the hardened sandbox —
            # see ``the orchestrator.sandbox.manager``. The container bind-mounts
            # the workspace, blocks egress to anything but
            # api.anthropic.com, and runs Claude as a non-root user
            # with no capabilities. The stdin/stdout NDJSON contract
            # is unchanged; reader threads don't care that the other
            # end is a docker process. Gated on ``_docker_mode_on``,
            # not ``_permission_mode``: with docker=true and bypass=false
            # the operator gets sandbox containment AND permission
            # prompts (the recommended posture).
            spawn_cwd: str | None = self._cwd
            if self._docker_mode_on:
                # Run the six sandbox pre-spawn steps (ensure image,
                # rate-check, name container, refuse workspace secrets,
                # wrap, audit-log) via the shared helper so the
                # streaming and one-shot paths stay in lockstep. The
                # audit log fires before the subprocess starts so the
                # operator has a record even if the container fails to
                # come up.
                mount_root, workdir_subpath = self._sandbox_mount()
                command, self._docker_container_name = wrap_spawn_for_docker(
                    command,
                    workspace_path=mount_root,
                    task_id=self._task_id,
                    logger=self.logger,
                    workdir_subpath=workdir_subpath,
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
            # Always print the session id + whether this is a fresh
            # spawn or a ``--resume``. This single line fires on every
            # spawn, so the operator can grep one task across an orchestrator
            # restart and confirm the id is the SAME before and after
            # (resume worked) vs. a new id (history was lost). Pinned
            # synchronously in ``_build_command`` so it's already set.
            self.logger.info(
                'started streaming claude session for task %s (pid %s) — '
                '%s session id %s',
                self._task_id,
                self._proc.pid,
                'resuming' if self._resume_session_id else 'fresh',
                self._agent_session_id or '(pending)',
            )
            self._spawn_reader_threads()

        if initial_prompt:
            self.send_user_message(initial_prompt)

    def send_user_message(
        self,
        text: str,
        images: list[dict] | None = None,
    ) -> None:
        """Push a follow-up user message into the live conversation.

        ``images`` is an optional list of ``{media_type, data}`` dicts
        where ``data`` is base64-encoded image bytes. Each one is
        appended to the message ``content`` array as an Anthropic
        ``image`` block, so the operator can paste a screenshot into
        the chat composer and have Claude actually see it.

        Empty text + no images is a no-op (legacy behaviour). Empty
        text **with** images sends the images alone, which the
        Anthropic API accepts ("here, look at this").
        """
        normalized = str(text or '').rstrip('\n')
        image_blocks = _validate_image_blocks(images or [])
        if not normalized and not image_blocks:
            return
        if not self.is_alive:
            raise RuntimeError(
                f'cannot send to streaming session for task {self._task_id}: '
                'subprocess is not running'
            )
        content: list[dict] = []
        if normalized:
            content.append({'type': 'text', 'text': normalized})
        content.extend(image_blocks)
        envelope = {
            'type': 'user',
            'message': {
                'role': 'user',
                'content': content,
            },
        }
        self._write_stdin_line(envelope)
        # Increment AFTER the write succeeds. ``_write_stdin_line`` can
        # raise (broken pipe, etc.) — only count messages we actually
        # handed to Claude. The counter is paired with
        # ``result_events_received`` to expose "in-flight messages" to
        # callers (``AgentService._task_has_busy_turn``).
        with self._user_messages_sent_lock:
            self._user_messages_sent += 1
            self._last_user_message_sent_epoch = time.time()
        self.logger.info(
            'forwarded user message to claude session for task %s '
            '(%s chars, %d image(s))',
            self._task_id,
            len(normalized),
            len(image_blocks),
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
        # Read the original input WITHOUT popping yet — if the stdin
        # write below fails (broken pipe, dead subprocess), the request
        # must stay in the live dict so the operator's orange-dot
        # indicator stays accurate and the next retry can find it.
        with self._pending_control_requests_lock:
            request = self._pending_control_requests.get(request_id_str, {})
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
        # Write FIRST; only pop on success. If write raises, the
        # caller re-tries with the same request_id and the operator
        # sees the orange dot stay until the response actually lands.
        self._write_stdin_line(envelope)
        with self._pending_control_requests_lock:
            self._pending_control_requests.pop(request_id_str, None)
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
        self._publish_event(synthetic_event)

    def publish_system_notice(
        self, subtype: str, message: str, extra: dict | None = None,
    ) -> None:
        """Inject a synthetic ``system`` event into the live + replayable
        feed. Generic on purpose — the caller (e.g. the webserver Action
        Guard) supplies the wire-protocol ``subtype`` and any structured
        ``extra`` payload, so this transport stays free of product-specific
        notice types. Mirrors how ``_maybe_warn_out_of_sandbox_write``
        surfaces an out-of-folder write."""
        raw: dict[str, Any] = {
            'type': CLAUDE_EVENT_SYSTEM,
            'subtype': str(subtype or ''),
            'message': str(message or ''),
        }
        if isinstance(extra, dict):
            raw.update(extra)
        self._publish_event(SessionEvent(raw=raw))

    def terminate(self, grace_seconds: float = 5.0) -> None:
        """Close stdin, wait briefly, then SIGTERM / kill as needed.

        Three-step escalation: each step gives the subprocess a chance to
        exit cleanly before the next, more forceful one. We hold the proc
        lock for the whole sequence so a concurrent ``start`` can't race.
        """
        with self._proc_lock:
            proc = self._proc
            if proc is not None:
                self._close_stdin_locked()
                if not _wait_for_exit(proc, max(0.1, float(grace_seconds))):
                    self._escalate_to_sigterm(proc)
                self._proc = None
        for thread in self._reader_threads:
            thread.join(timeout=1.0)
        self._reader_threads = []
        # Wake any SSE tailers blocked in ``wait_for_new_events`` so
        # they observe the freshly-flipped ``is_alive=False`` and
        # close the stream immediately, instead of sleeping out the
        # heartbeat interval.
        with self._events_changed:
            self._events_changed.notify_all()
        # The subprocess is gone, so any unanswered permission asks are dead —
        # drop them so a stopped session stops surfacing approval popups in the
        # operator's pending-permissions poll ("still see requests after Stop").
        with self._pending_control_requests_lock:
            self._pending_control_requests.clear()

    def _escalate_to_sigterm(self, proc: subprocess.Popen) -> None:
        self.logger.info(
            'streaming claude session for task %s did not exit; sending SIGTERM',
            self._task_id,
        )
        if not (_IS_WINDOWS and self._kill_tree_safely(proc)):
            # POSIX, or the tree kill could not run (no taskkill):
            # fall back to the single-process signal. On Windows
            # ``send_signal(SIGTERM)`` is ``TerminateProcess`` on the
            # DIRECT child only — ``claude`` usually resolves to the
            # npm ``claude.cmd`` shim there, so the direct child is a
            # cmd.exe wrapper and the real CLI (node) SURVIVES the
            # kill. The orphan keeps the session transcript open and
            # the next ``--resume`` silently starts a blank,
            # memoryless session (the "forgot everything after
            # stop/restart" bug) — which is why the tree kill is
            # always attempted first on Windows.
            self._send_signal_locked(signal.SIGTERM)
        if _wait_for_exit(proc, 2.0):
            return
        self._escalate_to_kill(proc)

    def _kill_tree_safely(self, proc: subprocess.Popen) -> bool:
        """Tree-kill ``proc`` (wrapper AND its CLI child); False on failure.

        Never raises — the terminate path must always fall through to
        the portable single-process kill rather than crash mid-teardown.
        """
        try:
            return bool(kill_process_tree(proc.pid, logger=self.logger))
        except Exception:
            self.logger.exception(
                'tree kill failed for streaming claude session %s (pid %s)',
                self._task_id,
                getattr(proc, 'pid', '?'),
            )
            return False

    def _escalate_to_kill(self, proc: subprocess.Popen) -> None:
        self.logger.warning(
            'streaming claude session for task %s ignored SIGTERM; killing',
            self._task_id,
        )
        if _IS_WINDOWS:
            # Same tree semantics as the SIGTERM step — see above. The
            # ``proc.kill()`` below stays as a last-resort fallback for
            # when taskkill itself is unavailable.
            self._kill_tree_safely(proc)
        # ``Popen.kill()`` is portable: SIGKILL on POSIX, ``TerminateProcess``
        # on Windows. ``signal.SIGKILL`` itself doesn't exist on Windows.
        try:
            proc.kill()
        except (ProcessLookupError, OSError):
            pass
        _wait_for_exit(proc, 2.0)
        if self._docker_container_name:
            # SIGKILL to ``proc`` (the wrapping ``docker run`` client) can
            # NEVER be forwarded to the container it started — unlike
            # SIGTERM, which the attached docker CLI does forward while
            # it's still alive to catch it. Without this, every session
            # that ignores SIGTERM leaks its container running forever;
            # ``--rm`` only fires on the container's OWN clean exit, never
            # as a side effect of the host client process dying.
            from sandbox_core_lib.sandbox_core_lib.manager import kill_container
            kill_container(self._docker_container_name, logger=self.logger)

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

    def events_after(self, start_index: int) -> tuple[list[SessionEvent], int]:
        """Return events appended at or after ``start_index`` (only the
        new slice) plus the new high-water index.

        Cheap O(new) read instead of the O(total) snapshot
        ``recent_events()`` makes — used by the SSE tail loop, which
        calls this once per wakeup to drain anything new without
        copying the whole event log every time.
        """
        with self._recent_events_lock:
            total = len(self._recent_events)
            if start_index < 0:
                start_index = 0
            if start_index >= total:
                return ([], total)
            return (list(self._recent_events[start_index:]), total)

    def wait_for_new_events(
        self,
        start_index: int,
        timeout: float,
    ) -> tuple[list[SessionEvent], int, bool]:
        """Block until at least one event has been appended past
        ``start_index`` OR ``timeout`` seconds elapse OR the session
        terminates.

        Returns ``(new_events, new_index, alive)``. ``alive=False``
        signals the SSE loop that it should emit a terminal frame and
        exit. The lock is held across the wait+drain so a concurrent
        ``_publish_event`` cannot land an event between the wait
        wake-up and the slice read.
        """
        with self._events_changed:
            self._events_changed.wait_for(
                lambda: (
                    len(self._recent_events) > start_index
                    or not self.is_alive
                ),
                timeout=timeout,
            )
            total = len(self._recent_events)
            new_events = (
                list(self._recent_events[start_index:total])
                if total > start_index
                else []
            )
            return (new_events, total, self.is_alive)

    def _publish_event(self, event: SessionEvent) -> None:
        """Append ``event`` to the recent-events log and wake up
        anyone blocked in ``wait_for_new_events``.

        Single funnel for the two append sites (real stdout events
        and the synthetic permission-response mirror) so neither path
        can forget to notify and silently strand a tailing client.
        Also feeds the legacy ``_event_queue`` for the
        ``poll_event`` / ``events_iter`` callers.
        """
        self._track_context_usage(event)
        with self._events_changed:
            self._recent_events.append(event)
            self._events_changed.notify_all()
        self._event_queue.put(event)

    def _track_context_usage(self, event: SessionEvent) -> None:
        """Remember how much context the latest turn occupied.

        The CLI reports per-turn ``usage`` on assistant and result events.
        The PROMPT side of it — fresh input plus both cache buckets — is the
        conversation as the model saw it, i.e. the context actually in use;
        output tokens are what it wrote, and become part of the next turn's
        prompt rather than adding to this one.

        Latest-wins rather than a sum: each turn re-sends the whole
        conversation, so the newest number IS the size, and adding them up
        would climb past the window while the real usage sat flat.

        Best-effort — a shape change upstream must never break the stream.
        """
        resolved = resolved_model_of_event(event.raw)
        # ONLY assistant events. Their ``usage`` is one API request's prompt —
        # which is the context size. The ``result`` event's ``usage`` is the
        # turn's CUMULATIVE total across every request in the agentic loop, so
        # on a long tool-using turn its cache_read alone is several times the
        # window. Latest-wins meant the result event overwrote the correct
        # figure at the end of every turn, and a 122k conversation in a 1M
        # window rendered "0% left" in red while the CLI's own ``/context``
        # said 12% used.
        raw = event.raw if isinstance(event.raw, dict) else {}
        tokens = (
            prompt_tokens_from_usage(usage_of_event(raw))
            if raw.get('type') == CLAUDE_EVENT_ASSISTANT
            else 0
        )
        if not resolved and tokens <= 0:
            return
        with self._context_usage_lock:
            if tokens > 0:
                self._context_used_tokens = tokens
            if resolved:
                self._resolved_model = resolved

    def context_usage(self) -> dict:
        """``{used_tokens, limit_tokens, model}`` for the context indicator.

        ``limit_tokens`` is 0 when the window can't be determined, which the
        UI must render as "unknown" rather than guessing a percentage — a
        wrong "93% full" would push an operator into compacting a session
        that had plenty of room.
        """
        with self._context_usage_lock:
            used = self._context_used_tokens
            resolved = self._resolved_model
        # Sized from the RESOLVED model id. Note the CLI strips any ``[1m]``
        # suffix before reporting that id, so the marker alone can't size the
        # window — ``context_window_tokens`` keys off the model FAMILY, whose
        # current generation is 1M for opus/sonnet/fable and 200k for haiku.
        # Sizing every session at 200k made a 97k conversation in a 1M window
        # read "51% left" while the CLI's own ``/context`` said 10% used.
        # Until a turn reports a model id the window is UNKNOWN (0), which the
        # meter renders as nothing rather than a confident wrong percentage.
        # ``widen_window_to_observed`` is the backstop for an id we haven't
        # learned: usage above the assumed window disproves the assumption.
        return {
            'used_tokens': used,
            'limit_tokens': widen_window_to_observed(
                context_window_tokens(resolved), used,
            ),
            'model': resolved,
        }

    def stderr_snapshot(self) -> list[str]:
        with self._stderr_lock:
            return list(self._stderr_lines)

    # ----- internals -----

    def _build_command(self) -> list[str]:
        binary_path = shutil.which(self._binary) or self._binary
        # Resolve PAST a Windows npm cmd-shim before spawning. cmd.exe
        # silently cuts its command line at the first raw newline (and
        # caps it at ~8K chars); the ``--append-system-prompt`` value
        # below is multiline, so spawning through ``claude.cmd``
        # dropped every later argument — including ``--resume`` /
        # ``--session-id`` / ``--add-dir`` — and Claude started a
        # fresh, memoryless session under a new id on every
        # respawn (the Windows resume-amnesia bug). Shared with the
        # one-shot client so both spawn paths bypass the shim the same
        # way.
        spawn_prefix = resolve_windows_cli_invocation(binary_path) or [binary_path]
        command: list[str] = [
            *spawn_prefix,
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
        # Force out-of-workspace file writes (e.g. /tmp scratch) back through
        # the permission path — acceptEdits otherwise auto-accepts them with no
        # approval. See write_scope_settings; the post-hoc warning backstops
        # any path the rules don't enumerate.
        from claude_core_lib.claude_core_lib.helpers.write_scope_settings import (
            out_of_workspace_write_settings_json,
        )
        command.extend(['--settings', out_of_workspace_write_settings_json(
            self._cwd, self._additional_dirs,
        )])
        if self._permission_prompt_tool:
            command.extend(['--permission-prompt-tool', self._permission_prompt_tool])
        # Session identity comes EARLY in the argv — before any
        # free-text value (``--append-system-prompt`` is multiline).
        # If the spawn ever degrades back to a cmd.exe shim (resolver
        # fallback), line truncation must cost us the tail of the
        # system prompt, never the ``--resume``/``--session-id`` pin.
        if self._resume_session_id:
            # ``claude --resume <id>`` keeps the same session id by
            # default — Claude only forks a new id when ``--fork-session``
            # is also passed. So just resuming is enough to stick with
            # the adopted id. Adopt the resume id synchronously so
            # callers reading ``agent_session_id`` before the first
            # ``system { subtype: init }`` event arrives get the right
            # answer; the actual id is re-confirmed via
            # ``_maybe_capture_session_id`` once the event lands.
            self._agent_session_id = self._resume_session_id
            command.extend(['--resume', self._resume_session_id])
        else:
            # Pin a session-id up front so callers can resume after restart
            # without waiting for the system event to arrive.
            self._agent_session_id = str(uuid.uuid4())
            command.extend(['--session-id', self._agent_session_id])
        append_additional_dirs(command, self._additional_dirs)
        append_model_effort_flags(
            command,
            model=self._model,
            max_turns=self._max_turns,
            effort=self._effort,
        )
        if self._allowed_tools:
            command.extend(['--allowedTools', self._allowed_tools])
        # Hard, non-overridable floor: the git denylist (the orchestrator is the only
        # component that ever runs git) PLUS the Action Guard no-legit-use
        # programs (mkfs / namespace-escape / host-power). Refused by the CLI
        # in every permission mode. See ClaudeCliClient.GIT_DENY_PATTERNS /
        # ACTION_GUARD_DENY_PATTERNS for rationale.
        from claude_core_lib.claude_core_lib.cli_client import ClaudeCliClient as _CliClient
        merged_disallowed = _CliClient._merge_disallowed_with_floor(
            self._disallowed_tools
        )
        command.extend(['--disallowedTools', merged_disallowed])
        # When ``the docker setting=true`` the agent gets a short
        # description of the sandboxed environment appended to its
        # system prompt. The composer joins the architecture doc,
        # learned lessons, and the addendum into one value because the
        # Claude CLI takes a single ``--append-system-prompt``. Shared
        # with ``ClaudeCliClient._build_command`` via
        # ``build_appended_system_prompt`` so streaming and one-shot
        # spawns deliver identical guidance to the agent.
        appended_system_prompt = build_appended_system_prompt(
            architecture_doc_path=self._architecture_doc_path,
            lessons_path=self._lessons_path,
            docker_mode_on=self._docker_mode_on,
            logger=self.logger,
        )
        if appended_system_prompt:
            # The one multiline, unbounded-length value — deliberately
            # LAST so a degraded batch-shim spawn truncates it instead
            # of the flags that matter (see the session-identity block
            # above).
            command.extend(['--append-system-prompt', appended_system_prompt])
        return command

    def _build_env(self) -> dict[str, str]:
        # Shared headless-Claude env invariant; the streaming path also
        # threads its per-session ``_env_overrides`` through first. See
        # ``build_claude_subprocess_env``.
        return build_claude_subprocess_env(self._env_overrides)

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
            if event.is_terminal:
                self._terminal_event = event
                # Output-side credential scan on the assembled final
                # text — closes residual #18 on the streaming path.
                # Mirrors ClaudeCliClient._scan_response_for_credentials
                # so the one-shot and streaming spawns produce identical
                # audit signal. Detective-only: the agent's text has
                # already crossed to Anthropic.
                self._scan_terminal_for_credentials(event)
            # Capture + sandbox-annotate the control request BEFORE
            # publishing: ``_publish_event`` appends to ``_recent_events``
            # and wakes SSE tailers, so annotating after it would race a
            # tailer that serializes the event in the gap (the
            # ``outside_sandbox`` flag must be on the raw before it ships).
            self._maybe_capture_control_request(event)
            self._publish_event(event)
            self._maybe_warn_out_of_sandbox_write(event)
            self._maybe_capture_session_id(event)
            self._maybe_fire_done_sentinel(event)
            self._log_event_for_operator(event)
                # Don't break here — let the subprocess close stdout itself.
        # stdout closed; the subprocess is winding down or already gone.
        # Wake any SSE tailers blocked in ``wait_for_new_events`` so
        # they observe the impending ``is_alive=False`` without having
        # to sleep through the heartbeat interval. Same rationale as
        # the explicit ``terminate`` path above.
        with self._events_changed:
            self._events_changed.notify_all()

    def _scan_terminal_for_credentials(self, event: SessionEvent) -> None:
        """WARNING-log credential AND phishing patterns in terminal text.

        Delegates to the shared
        :func:`...helpers.credential_scan.
        scan_text_for_credentials_and_phishing` (same helper the
        one-shot ``ClaudeCliClient`` uses) so the two paths produce the
        same audit signal. Pattern names + redacted previews only —
        full values are never logged. See ``BYPASS_PROTECTIONS.md``
        residuals #16 (phishing) and #18 (credential exfil).
        """
        raw = event.raw or {}
        result_text = str(raw.get('result', '') or '')
        scan_text_for_credentials_and_phishing(
            result_text,
            logger=self.logger,
            context_label=f'streaming Claude session for task {self._task_id}',
        )

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
        """Detect the done-sentinel in an assistant text block and fire once.

        The host's wait-planning prompt instructs the agent to end its final
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
        sentinel = self._done_sentinel
        for block in content:
            if not isinstance(block, dict) or block.get('type') != 'text':
                continue
            text = str(block.get('text', '') or '')
            if sentinel in text:
                self._done_sentinel_fired = True
                self.logger.info(
                    'task %s: detected %s in assistant message — '
                    'firing done callback',
                    self._task_id, sentinel,
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
        self._annotate_sandbox_scope(event, request)

    def _annotate_sandbox_scope(self, event: SessionEvent, request: dict) -> None:
        """Flag a permission ask that reaches outside the task sandbox.

        Writes ``outside_sandbox``/``outside_path`` onto the event's raw
        payload (the dict forwarded over SSE) so the planning UI can
        shout a warning and withhold the *remembered* approval scope —
        an "allow always" for a path outside the task folder would hand
        the agent standing out-of-sandbox access on every future run.
        The classification is purely lexical (see ``sandbox_scope``); on
        any error we leave the event unflagged (fail-open on the WARNING,
        never fail-closed into a crash of the permission pipeline).
        """
        try:
            outside, offending = self._classify_sandbox(request.get('input') or {})
        except Exception:
            return
        if outside:
            event.raw['outside_sandbox'] = True
            event.raw['outside_path'] = offending

    def _classify_sandbox(self, tool_input) -> tuple[bool, str]:
        """``(outside, offending_path)`` for a tool input — structured path
        args first, then (for Bash) the command's absolute path arguments.

        One place so the live SSE annotation and the global pending-permissions
        feed classify identically."""
        outside, offending = classify_tool_input_sandbox(
            tool_input, self._cwd, self._additional_dirs,
            self._sandbox_allowed_paths,
        )
        if outside:
            return outside, offending
        command = tool_input.get('command') if isinstance(tool_input, dict) else ''
        if command:
            outside, offending = classify_command_sandbox(
                command, self._cwd, self._additional_dirs,
                self._sandbox_allowed_paths,
            )
            if outside:
                return outside, offending
            # A container-runtime / privilege escape (docker, sudo, …) reaches
            # the host AROUND any path sandbox — treat it as out-of-sandbox so
            # it gets the red warning and no remembered grant.
            escapes, program = classify_command_escape(command)
            if escapes:
                return True, f'{program} (runs outside the task sandbox)'
        return False, ''

    # Tools that WRITE to the filesystem. A self-authorized write outside
    # the task folder is the one we must never let pass silently.
    _SANDBOX_WRITE_TOOLS = frozenset({
        'Write', 'Edit', 'MultiEdit', 'NotebookEdit',
    })

    def _maybe_warn_out_of_sandbox_write(self, event: SessionEvent) -> None:
        """Inject a loud chat warning when the agent WRITES outside the task.

        the orchestrator's permission-time warning only fires on tool calls Claude
        routes to it as a ``control_request``. Under ``acceptEdits`` the
        CLI auto-accepts writes to scratch paths (e.g. ``/tmp``) WITHOUT
        asking, so those never reached the permission path and slipped by
        unnoticed. This scans the live ``assistant`` tool-use stream
        directly and emits a synthetic ``system``/sandbox-warning event
        for each NEW out-of-folder write path — so an out-of-task write is
        always visible, even when the orchestrator couldn't gate it. Best-effort: any
        parse error leaves the stream untouched.
        """
        if event.event_type != 'assistant':
            return
        try:
            warnings = self._out_of_sandbox_write_paths(event)
        except Exception:
            return
        for path in warnings:
            if path in self._sandbox_warned_paths:
                continue
            self._sandbox_warned_paths.add(path)
            self._publish_event(SessionEvent(raw={
                'type': CLAUDE_EVENT_SYSTEM,
                'subtype': CLAUDE_SYSTEM_SUBTYPE_SANDBOX_WARNING,
                'outside_path': path,
                'message': (
                    f'Claude wrote OUTSIDE the task folder: {path} — no '
                    'approval was requested (the CLI auto-accepts scratch '
                    'paths like /tmp). Review this change.'
                ),
            }))
            self.logger.warning(
                'task %s: agent wrote outside the task sandbox without a '
                'permission request: %s', self._task_id, path,
            )

    def _out_of_sandbox_write_paths(self, event: SessionEvent) -> list[str]:
        """Out-of-folder file paths from the WRITE tool_use blocks of an
        ``assistant`` event (empty when none / not an assistant turn)."""
        raw = event.raw if isinstance(event.raw, dict) else {}
        message = raw.get('message')
        content = message.get('content') if isinstance(message, dict) else None
        if not isinstance(content, list):
            return []
        paths: list[str] = []
        for block in content:
            if not isinstance(block, dict) or block.get('type') != 'tool_use':
                continue
            if str(block.get('name') or '') not in self._SANDBOX_WRITE_TOOLS:
                continue
            tool_input = block.get('input')
            outside, offending = classify_tool_input_sandbox(
                tool_input, self._cwd, self._additional_dirs,
                self._sandbox_allowed_paths,
            )
            if outside and offending not in paths:
                paths.append(offending)
        return paths

    def pending_control_request_tool(self) -> str:
        """Tool name on the oldest currently-waiting control request, or ''.

        Reads the LIVE ``_pending_control_requests`` dict — the
        authoritative "agent is paused on stdin, needs an answer"
        state, populated when a ``control_request`` arrives and
        ``pop``'d when the operator's response is delivered. This is
        what the orange-tab indicator should track. The previous
        approach walked ``recent_events`` history, which sometimes
        showed "still waiting" after the response had landed (the
        synthetic ``permission_response`` was dropped by client-side
        dedupe, or the walk hit an old un-answered request that the
        agent had since moved past). The dict version flips false
        the instant ``send_permission_response`` runs, so the tab
        clears as soon as auto-allow / manual-allow completes.

        Returns the tool name from the oldest pending request (FIFO
        on insertion order — matches operator expectation that the
        modal shows the first un-answered ask). Empty string when
        nothing is pending.
        """
        with self._pending_control_requests_lock:
            for request in self._pending_control_requests.values():
                if not isinstance(request, dict):
                    continue
                tool_name = str(
                    request.get('tool_name')
                    or request.get('tool')
                    or '',
                ).strip()
                return tool_name or '<unknown>'
        return ''

    def pending_control_requests(self) -> list[dict[str, Any]]:
        """Full envelopes for every currently-waiting control request, oldest
        first.

        Powers the GLOBAL permission feed: the per-task SSE stream only
        delivers a ``control_request`` to the ONE browser tab that has that
        session open, so a permission ask on a BACKGROUNDED task would never
        reach the operator until they happened to click into it. This lets
        the webserver surface every unanswered ask across all live sessions,
        so the modal can pop no matter which task is in focus.

        Each envelope is shaped exactly like the ``control_request`` event
        the SSE path emits (``type``/``request_id``/``request``), so the same
        ``unpackPermissionEnvelope`` + ``PermissionModal`` render it
        unchanged — including the lexical sandbox-scope flags (the live
        capture annotates the EVENT, not the stored request, so we re-derive
        them here; fail-open, never crash the feed)."""
        with self._pending_control_requests_lock:
            items = list(self._pending_control_requests.items())
        envelopes: list[dict[str, Any]] = []
        for request_id, request in items:
            if not isinstance(request, dict):
                continue
            envelope: dict[str, Any] = {
                'type': CLAUDE_EVENT_CONTROL_REQUEST,
                'request_id': request_id,
                'request': request,
            }
            try:
                outside, offending = self._classify_sandbox(request.get('input') or {})
                if outside:
                    envelope['outside_sandbox'] = True
                    envelope['outside_path'] = offending
            except Exception:
                pass
            envelopes.append(envelope)
        return envelopes

    def _log_event_for_operator(self, event: SessionEvent) -> None:
        """Surface high-signal events to the orchestrator terminal log.

        The planning UI shows everything; the operator running the orchestrator wants
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
        # Shared strip → ``json.loads`` → dict-check (see
        # ``index.parse_jsonl_dict_line``); the streaming path keeps its
        # operator-facing warning by logging when the line could not be
        # turned into an event dict.
        payload = parse_jsonl_dict_line(text)
        if payload is None:
            self.logger.warning(
                'streaming claude session %s emitted non-JSON line: %s',
                self._task_id,
                condensed_text(text)[:240],
            )
            return None
        return SessionEvent(raw=payload)

    def _maybe_capture_session_id(self, event: SessionEvent) -> None:
        candidate = fix_session_id(event.raw.get('session_id', ''))
        if not candidate:
            return
        if not self._agent_session_id:
            self._agent_session_id = candidate
            return
        # Only init is authoritative; later events can echo fixture ids.
        is_init = (
            event.raw.get('type') == CLAUDE_EVENT_SYSTEM
            and event.raw.get('subtype') == 'init'
        )
        if not is_init:
            return
        if candidate == self._agent_session_id:
            if self._resume_session_id:
                self._resume_confirmed = True
            if not self._session_id_confirmed:
                self.logger.info(
                    'task %s: claude confirmed %s session id %s',
                    self._task_id,
                    'resumed' if self._resume_session_id else 'fresh',
                    candidate,
                )
                self._session_id_confirmed = True
        elif not self._session_id_mismatch_logged:
            mode = 'resume' if self._resume_session_id else 'fresh'
            action = (
                'keeping the requested resume id'
                if self._resume_session_id
                else 'adopting claude\'s actual id'
            )
            self.logger.warning(
                'task %s: claude reported session id %s but the orchestrator '
                'expected %s (%s) — %s',
                self._task_id,
                candidate,
                self._agent_session_id,
                mode,
                action,
            )
            self._session_id_mismatch_logged = True
            self._session_id_confirmed = True  # suppress duplicate "confirmed" on next call
            if self._resume_session_id:
                # The CLI ignored --resume and started a fresh session
                # under a new id — a conversation with no memory of the
                # task. Flag it so the manager terminates this impostor
                # instead of letting it masquerade as the resumed chat.
                self._resume_ignored = True
                return
            # Fresh spawn: adopt the id Claude actually wrote to.
            self._agent_session_id = candidate
            if callable(self._session_id_correction_callback):
                try:
                    self._session_id_correction_callback(candidate)
                except Exception:
                    self.logger.exception(
                        'task %s: session_id_correction_callback raised',
                        self._task_id,
                    )

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
