"""One message to a task's main chat, and the reply that answers it.

Through the channel the review loop and the diff-comment runs use (one sender
per task, so they never both send on one idle moment): wait for the chat to be
free, send, then read the turn that ends after the send. The plan queue asks
for its split this way, and a plan sub-task's question goes to the chat of the
task its plan was split from this way.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from review_loop_core_lib.review_loop_core_lib.ports import Readiness

POLL_SECONDS = 2.0


class ChatQuestionCancelled(RuntimeError):
    """Whoever asked stopped waiting before the chat answered."""


class ChatQuestion(object):
    def __init__(
        self, *, chat, timeout_seconds: int, clock: Callable[[], float] = time.time,
        poll_seconds: float = POLL_SECONDS,
    ) -> None:
        self._chat = chat
        self._timeout = float(timeout_seconds)
        self._clock = clock
        self._poll = poll_seconds

    def ask(self, task_id: str, message: str, *, cancel_event: threading.Event) -> str:
        """The text of the chat's reply to ``message``. Raises
        ``ChatQuestionCancelled`` when ``cancel_event`` is set first,
        ``TimeoutError`` past the timeout, ``RuntimeError`` when it can't be
        asked or the turn ends in an error."""
        deadline = self._clock() + self._timeout
        self._wait_until_free(task_id, cancel_event, deadline)
        with self._chat.dispatch_lock(task_id):
            sent_at = self._clock()
            if not self._chat.deliver(task_id, message, force_respawn=self._chat.is_stalled(task_id)):
                raise RuntimeError('the task\'s chat could not be reached')
        return self._reply(task_id, sent_at, cancel_event, deadline)

    def _wait_until_free(self, task_id: str, cancel_event: threading.Event, deadline: float) -> None:
        while True:
            readiness = self._chat.readiness(task_id)
            if readiness.state == Readiness.READY:
                return
            if readiness.state == Readiness.REFUSE:
                raise RuntimeError(readiness.reason)
            self._pause(cancel_event, deadline, f'the chat stayed busy: {readiness.reason}')

    def _reply(self, task_id: str, sent_at: float, cancel_event: threading.Event, deadline: float) -> str:
        while True:
            turn = self._chat.turn_end_since(task_id, sent_at)
            if turn is not None:
                if turn.is_error:
                    raise RuntimeError('the chat\'s turn ended in an error')
                return turn.text
            self._pause(cancel_event, deadline, 'the chat did not answer in time')

    def _pause(self, cancel_event: threading.Event, deadline: float, why: str) -> None:
        if cancel_event.is_set():
            raise ChatQuestionCancelled('stopped waiting for the chat')
        if self._clock() >= deadline:
            raise TimeoutError(why)
        cancel_event.wait(self._poll)
