"""The UI recognises the plan queue's split request by its header — keep them in step.

``PLAN_SPLIT_CHAT_HEADER`` (Python) opens every request the queue sends into
the task's chat; ``planQueuePrompt.js`` (UI) finds that line to show the
message as kato's (in its own colour) instead of "You asked". If either side's
wording drifts, the UI silently puts kato's words in the operator's mouth — so
this reads the JS pattern straight out of the source and runs it against what
the Python side really sends.
"""
from __future__ import annotations

import re
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path

from review_loop_core_lib.review_loop_core_lib.ports import ChatReadiness, ChatTurnEnd

from kato_core_lib.data_layers.service.plan_queue.split_in_chat import ChatSplitProposer
from kato_core_lib.helpers.plan_queue_guidance import (
    PLAN_QUESTION_CHAT_HEADER,
    PLAN_SPLIT_CHAT_HEADER,
    plan_question_message,
)

_JS = (
    Path(__file__).resolve().parents[1]
    / 'webserver' / 'ui' / 'src' / 'components' / 'planQueue' / 'planQueuePrompt.js'
)


def _ui_pattern(name: str = 'PLAN_SPLIT_HEADER_PATTERN') -> re.Pattern:
    match = re.search(rf'{name} = /(.+)/m;', _JS.read_text(encoding='utf-8'))
    assert match, f'{name} not found in planQueuePrompt.js'
    return re.compile(match.group(1), re.MULTILINE)


class _Chat(object):
    def __init__(self) -> None:
        self.sent = []

    def readiness(self, task_id):
        return ChatReadiness.ready()

    @contextmanager
    def dispatch_lock(self, task_id):
        yield

    def is_stalled(self, task_id):
        return False

    def deliver(self, task_id, prompt, *, force_respawn):
        self.sent.append(prompt)
        return True

    def turn_end_since(self, task_id, since):
        return ChatTurnEnd(received_at=since + 1, text='ok')


class HeaderPinTests(unittest.TestCase):
    def test_the_ui_pattern_matches_the_header_python_writes(self) -> None:
        self.assertIsNotNone(_ui_pattern().search(PLAN_SPLIT_CHAT_HEADER))

    def test_it_matches_the_request_really_sent_even_behind_a_preamble(self) -> None:
        chat = _Chat()
        ChatSplitProposer(chat=chat, timeout_seconds=60, poll_seconds=0).propose(
            'Split the approved plan.', source_task_id='UNA-1', cancel_event=threading.Event(),
        )
        self.assertIsNotNone(_ui_pattern().search(chat.sent[0]))
        self.assertIsNotNone(_ui_pattern().search('WORKSPACE SCOPE …\n\n' + chat.sent[0]))


class QuestionHeaderPinTests(unittest.TestCase):
    """A plan sub-task's question in its plan's chat is shown as kato's, naming the sub-task."""

    def test_the_ui_pattern_reads_the_asking_task_off_the_real_message(self) -> None:
        pattern = _ui_pattern('PLAN_QUESTION_HEADER_PATTERN')
        self.assertEqual(pattern.search(PLAN_QUESTION_CHAT_HEADER.format(task_id='UNA-3274')).group(1), 'UNA-3274')
        message = plan_question_message('UNA-3274', '1. Which way?')
        self.assertEqual(pattern.search('WORKSPACE SCOPE …\n\n' + message).group(1), 'UNA-3274')
        self.assertIsNone(pattern.search(PLAN_SPLIT_CHAT_HEADER))


if __name__ == '__main__':
    unittest.main()
