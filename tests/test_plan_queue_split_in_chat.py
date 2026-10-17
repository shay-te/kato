"""The split asked for in the task's chat, where the operator watches and steers it.

A scripted chat stands in for the task's main chat (the review loop's channel):
when it is free, what it was sent, and the turn that answered.
"""

from __future__ import annotations

import threading
import unittest
from contextlib import contextmanager
from types import SimpleNamespace

from review_loop_core_lib.review_loop_core_lib.ports import ChatReadiness, ChatTurnEnd, Readiness

from kato_core_lib.data_layers.service.plan_queue.split_in_chat import (
    ChatSplitConversation,
    ChatSplitProposer,
    SplitCancelled,
)
from kato_core_lib.data_layers.service.review_loop_adapters import KatoChatChannel
from kato_core_lib.helpers.plan_queue_guidance import PLAN_SPLIT_CHAT_CLOSING, PLAN_SPLIT_CHAT_HEADER


class Clock(object):
    """Every read is a second later — a wait loop always moves on."""

    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        self.now += 1.0
        return self.now


class ScriptedChat(object):
    def __init__(self, readiness=(), turns=(), delivered=True, stalled=False) -> None:
        self.readiness_script = list(readiness) or [ChatReadiness.ready()]
        self.turns = list(turns)
        self.delivered = delivered
        self.stalled = stalled
        self.sent: list[tuple[str, str, bool]] = []
        self.locked: list[str] = []
        self.on_turn_read = None

    def readiness(self, task_id):
        return self.readiness_script.pop(0) if len(self.readiness_script) > 1 else self.readiness_script[0]

    @contextmanager
    def dispatch_lock(self, task_id):
        self.locked.append(task_id)
        yield

    def is_stalled(self, task_id):
        return self.stalled

    def deliver(self, task_id, prompt, *, force_respawn):
        self.sent.append((task_id, prompt, force_respawn))
        return self.delivered

    def turn_end_since(self, task_id, since):
        if self.on_turn_read is not None:
            self.on_turn_read()
        return self.turns.pop(0) if self.turns else None


class ChatSplitProposerTests(unittest.TestCase):
    def _propose(self, chat, *, timeout=1000, cancel=None):
        proposer = ChatSplitProposer(chat=chat, timeout_seconds=timeout, clock=Clock(), poll_seconds=0)
        return proposer.propose('Split the plan.', source_task_id='UNA-1', cancel_event=cancel or threading.Event())

    def test_waits_for_a_free_chat_sends_the_request_and_reads_the_answer(self) -> None:
        chat = ScriptedChat(readiness=[ChatReadiness.wait('the agent is mid-turn'), ChatReadiness.ready()],
                            turns=[None, ChatTurnEnd(received_at=200.0, text='Here it is <task-split>…')])
        self.assertEqual(self._propose(chat), 'Here it is <task-split>…')
        task_id, message, respawn = chat.sent[0]
        self.assertEqual((task_id, respawn, chat.locked), ('UNA-1', False, ['UNA-1']))
        self.assertTrue(message.startswith(f'{PLAN_SPLIT_CHAT_HEADER}\n\nSplit the plan.'))
        self.assertTrue(message.endswith(PLAN_SPLIT_CHAT_CLOSING))

    def test_a_stalled_chat_is_respawned_to_take_the_request(self) -> None:
        chat = ScriptedChat(stalled=True, turns=[ChatTurnEnd(received_at=1.0, text='ok')])
        self._propose(chat)
        self.assertTrue(chat.sent[0][2])

    def test_what_stops_a_split(self) -> None:
        cases = [
            (ScriptedChat(readiness=[ChatReadiness.refuse('kato is running this task')]), RuntimeError,
             'kato is running this task'),
            (ScriptedChat(delivered=False), RuntimeError, 'could not be reached'),
            (ScriptedChat(turns=[ChatTurnEnd(received_at=1.0, is_error=True)]), RuntimeError, 'ended in an error'),
            (ScriptedChat(readiness=[ChatReadiness.wait('busy')]), TimeoutError, 'the chat stayed busy: busy'),
            (ScriptedChat(turns=[]), TimeoutError, 'did not answer in time'),
        ]
        for chat, error, message in cases:
            with self.subTest(message), self.assertRaises(error) as caught:
                self._propose(chat, timeout=5)
            self.assertIn(message, str(caught.exception))

    def test_a_cancel_stops_the_wait_before_and_after_sending(self) -> None:
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(SplitCancelled):
            self._propose(ScriptedChat(readiness=[ChatReadiness.wait('busy')]), cancel=cancel)
        cancel = threading.Event()
        chat = ScriptedChat()
        chat.on_turn_read = cancel.set
        with self.assertRaises(SplitCancelled):
            self._propose(chat, cancel=cancel)
        self.assertEqual(len(chat.sent), 1)


class ConversationTests(unittest.TestCase):
    def test_the_newest_finished_reply_is_the_operators_steering(self) -> None:
        chat = ScriptedChat(turns=[None, ChatTurnEnd(received_at=3.0, is_error=True),
                                   ChatTurnEnd(received_at=4.0, text='merged them')])
        conversation = ChatSplitConversation(chat=chat)
        self.assertIsNone(conversation.latest_reply('UNA-1', 0.0))
        self.assertIsNone(conversation.latest_reply('UNA-1', 0.0))
        self.assertEqual(conversation.latest_reply('UNA-1', 0.0), (4.0, 'merged them'))


class ChannelModeTests(unittest.TestCase):
    def _channel(self, **kwargs):
        delivery = SimpleNamespace(runner_in_flight=lambda task_id: False, has_busy_turn=lambda task_id: False,
                                   is_stalled=lambda task_id: False)
        comment_runs = SimpleNamespace(chat_delivery=delivery, has_local_comment_pending=lambda task_id: False)
        return KatoChatChannel(comment_runs=comment_runs, session_manager=None, workspace_manager=None, **kwargs)

    def test_whether_a_chat_mode_refuses_is_the_callers_call(self) -> None:
        refused = self._channel(mode_refusal=lambda task_id: 'not in this mode').readiness('UNA-1')
        self.assertEqual((refused.state, refused.reason), (Readiness.REFUSE, 'not in this mode'))
        self.assertEqual(self._channel(mode_refusal=lambda task_id: '').readiness('UNA-1').state, Readiness.READY)

    def test_what_it_sends_is_named_for_whoever_sends_it(self) -> None:
        sent = []
        channel = self._channel(label='plan split')
        channel._get_comment_runs().chat_delivery.deliver = lambda task_id, prompt, **kwargs: sent.append(
            (task_id, prompt, kwargs['label'], kwargs['force_respawn'])) or True
        channel._get_session_manager = lambda: None
        channel._get_workspace_manager = lambda: None
        self.assertTrue(channel.deliver('UNA-1', 'Split the plan.', force_respawn=False))
        self.assertEqual(sent, [('UNA-1', 'Split the plan.', 'plan split', False)])


if __name__ == '__main__':
    unittest.main()
