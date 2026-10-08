"""What a host provides so a review loop can run against its tasks.

The loop itself is generic: review, report, wait for the fix, review again. It
knows nothing about how a host's chat sessions are spawned, which agent CLI
does the reviewing, or how a task's diff is computed. Those come in through
these interfaces, implemented by the host and handed to ``ReviewLoopService``.
"""

from __future__ import annotations

import threading
from contextlib import AbstractContextManager
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Protocol

from review_loop_core_lib.review_loop_core_lib.data.state import ReviewLoopState


@dataclass(frozen=True)
class RepoDiff(object):
    """One repository's changes against its base, as the reviewer should see them."""

    repo_id: str
    diff: str
    base: str = ''
    head: str = ''
    cwd: str = ''
    error: str = ''


# ``task_id -> every repository's diff``. Called once per review round, so a
# fix the chat just made is in the next review.
TaskDiffSource = Callable[[str], list[RepoDiff]]


class Readiness(str, Enum):
    READY = 'ready'      # nothing in flight: a message may go in now
    WAIT = 'wait'        # busy for now; ask again shortly
    REFUSE = 'refuse'    # this chat must not take a loop message at all


@dataclass(frozen=True)
class ChatReadiness(object):
    state: Readiness
    reason: str = ''     # human wording for WAIT / REFUSE ("the agent is mid-turn")

    @classmethod
    def ready(cls) -> 'ChatReadiness':
        return cls(Readiness.READY)

    @classmethod
    def wait(cls, reason: str) -> 'ChatReadiness':
        return cls(Readiness.WAIT, reason)

    @classmethod
    def refuse(cls, reason: str) -> 'ChatReadiness':
        return cls(Readiness.REFUSE, reason)


@dataclass(frozen=True)
class ChatTurnEnd(object):
    """A finished turn of the task's chat."""

    received_at: float
    is_error: bool = False
    # The turn's final reply. The loop reads the fixer's decisions from it.
    text: str = ''


class ChatChannel(Protocol):
    """The task's main chat, as the loop needs it."""

    def readiness(self, task_id: str) -> ChatReadiness:
        """May a loop message go into the chat now?"""

    def dispatch_lock(self, task_id: str) -> AbstractContextManager:
        """The lock EVERY sender into this chat holds across check → send."""

    def is_stalled(self, task_id: str) -> bool:
        """Alive but no longer reading input — a send must respawn it."""

    def session_alive(self, task_id: str) -> bool:
        """Is there a running chat session for the task at all?"""

    def pending_approval(self, task_id: str) -> str:
        """The tool the chat is waiting on the operator to approve, or ''.

        A fix turn paused on an approval is not stuck — it is waiting for a
        person, and the loop says so instead of just "fixing"."""

    def deliver(self, task_id: str, prompt: str, *, force_respawn: bool) -> bool:
        """Put ``prompt`` into the chat as a user message (spawning if needed)."""

    def turn_end_since(self, task_id: str, since_epoch: float) -> ChatTurnEnd | None:
        """The chat's newest finished turn after ``since_epoch``, if any."""


class Reviewer(Protocol):
    """Runs one fresh, read-only review and returns the reviewer's full reply."""

    def review(
        self, prompt: str, *, task_id: str, cancel_event: threading.Event, model: str = '',
    ) -> str:
        """Raises (anything) on failure; is expected to stop soon after
        ``cancel_event`` is set. ``model`` is the loop's pick ('' = the
        reviewer's own default)."""


@dataclass(frozen=True)
class LoopWording(object):
    """The host's own text for the two prompts the loop writes.

    ``wrap_untrusted`` frames text the host does not trust (ticket text, the
    diff, the reviewer's reply) so the agent reading it treats it as data,
    never as instructions. It has no safe default, so it is required.
    """

    wrap_untrusted: Callable[[str, str], str]
    findings_header: str = 'Review loop — round {round} of {max_rounds}'
    # The first line of the loop's OTHER chat messages — the self-check and the
    # test run — formatted with ``{stage}`` ("self-check 1 of 3", "run the
    # tests", "fix the failing tests"), so a UI can tell them apart from the
    # operator's own messages just like the findings.
    stage_header: str = 'Review loop — {stage}'
    reviewer_guidance: str = ''
    findings_guidance: str = ''


# ``(state, event)`` after every step the loop takes; ``event`` is one of
# ``started``, ``resumed``, ``reviewing``, ``reviewed``, ``sent``, ``nudged``,
# ``fixed``, ``finished``.
LoopObserver = Callable[[ReviewLoopState, str], None]
