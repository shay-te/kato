"""The split is proposed in the task's own chat.

There the operator watches the agent read the plan and work out the tasks,
and can tell it what to change ("merge those two", "we already have the
table") — the same chat the review loop's turns show in. The request is one
``ChatQuestion``: wait for the chat to be free, send, read the reply.

The conversation goes on after that: ``ChatSplitConversation`` hands the queue
the chat's newer replies, and one that carries a new split replaces the
proposal.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from kato_core_lib.data_layers.service.chat_question import POLL_SECONDS, ChatQuestion, ChatQuestionCancelled
from kato_core_lib.helpers.plan_queue_guidance import PLAN_SPLIT_CHAT_CLOSING, PLAN_SPLIT_CHAT_HEADER


class SplitCancelled(ChatQuestionCancelled):
    """The operator cancelled the split while it waited on the chat."""


class ChatSplitProposer(object):
    def __init__(
        self, *, chat, timeout_seconds: int, clock: Callable[[], float] = time.time,
        poll_seconds: float = POLL_SECONDS,
    ) -> None:
        self._question = ChatQuestion(
            chat=chat, timeout_seconds=timeout_seconds, clock=clock, poll_seconds=poll_seconds,
        )

    def propose(
        self, prompt: str, *, source_task_id: str, cancel_event: threading.Event, model: str = '',
    ) -> str:
        """The chat's reply to the split request. ``model`` is not used: the
        chat answers on its own model."""
        message = f'{PLAN_SPLIT_CHAT_HEADER}\n\n{prompt}\n\n{PLAN_SPLIT_CHAT_CLOSING}'
        try:
            return self._question.ask(source_task_id, message, cancel_event=cancel_event)
        except ChatQuestionCancelled as exc:
            raise SplitCancelled('the split was cancelled') from exc


class ChatSplitConversation(object):
    """The chat's replies after the split was proposed — the operator steering it."""

    def __init__(self, *, chat) -> None:
        self._chat = chat

    def latest_reply(self, source_task_id: str, since: float) -> tuple[float, str] | None:
        turn = self._chat.turn_end_since(source_task_id, since)
        if turn is None or turn.is_error:
            return None
        return turn.received_at, turn.text
