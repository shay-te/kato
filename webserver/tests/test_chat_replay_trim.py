"""Replayed chat history carries only what the chat reads.

A long chat's replay was 14.6 MB on every connect — opening the task, a refresh,
an idle tab reconnecting — and more than half of it was fields no part of the UI
touches: the CLI's second copy of every tool's output, per-message token counts,
tool-result bodies the chat never displays, bookkeeping ids. Each record is now
trimmed on its way out. What the chat renders, and what it identifies a message
by when history and the live stream both deliver it, must survive.
"""
from __future__ import annotations

import copy
import json
import unittest
from unittest.mock import patch

from kato_webserver import app as app_module

TOOL_OUTPUT = 'a very long command output line\n' * 200

USER_RECORD = {
    'type': 'user', 'uuid': 'u-1', 'timestamp': '2026-09-15T08:00:00.000Z',
    'sessionId': 's-1', 'cwd': '/w/UNA-1/repo', 'parentUuid': 'u-0',
    'gitBranch': 'UNA-1', 'userType': 'external', 'slug': 'x', 'promptId': 'p-1',
    'sourceToolAssistantUUID': 'a-1', 'isSidechain': False,
    'toolUseResult': {'stdout': TOOL_OUTPUT},
    'message': {
        'role': 'user',
        'content': [
            {'type': 'tool_result', 'tool_use_id': 't-1', 'is_error': False, 'content': TOOL_OUTPUT},
            {'type': 'text', 'text': 'what did the command print?'},
        ],
    },
}
ASSISTANT_RECORD = {
    'type': 'assistant', 'uuid': 'u-2', 'timestamp': '2026-09-15T08:00:05.000Z',
    'message': {
        'id': 'msg-2', 'role': 'assistant', 'model': 'claude-opus',
        'usage': {'input_tokens': 1200, 'output_tokens': 80, 'cache_read_input_tokens': 9000},
        'content': [
            {'type': 'tool_use', 'id': 't-1', 'name': 'Bash', 'input': {'command': 'ls -la'}},
            {'type': 'text', 'text': 'It listed the folder.'},
        ],
    },
}


def _replay(records):
    with patch(
        'claude_core_lib.claude_core_lib.session.history.load_history_events',
        return_value=records,
    ):
        pairs = list(app_module._replay_history_from_disk('s-1'))
    return [
        (epoch, json.loads(frame.split('data: ', 1)[1])['event'])
        for epoch, frame in pairs
    ]


class ReplayedHistoryIsTrimmedTests(unittest.TestCase):
    def test_fields_the_chat_never_reads_are_not_sent(self) -> None:
        (_epoch, user), (_e, assistant) = _replay([USER_RECORD, ASSISTANT_RECORD])
        for field in ('toolUseResult', 'cwd', 'parentUuid', 'gitBranch', 'userType',
                      'slug', 'promptId', 'sourceToolAssistantUUID', 'isSidechain'):
            self.assertNotIn(field, user['raw'], field)
        self.assertNotIn('usage', assistant['raw']['message'])

    def test_a_tool_result_keeps_its_identity_but_not_its_body(self) -> None:
        (_epoch, user), = _replay([USER_RECORD])
        result = user['raw']['message']['content'][0]
        self.assertEqual(result, {'type': 'tool_result', 'tool_use_id': 't-1', 'is_error': False})

    def test_everything_the_chat_renders_or_matches_on_survives(self) -> None:
        (_e1, user), (_e2, assistant) = _replay([USER_RECORD, ASSISTANT_RECORD])
        self.assertEqual(user['raw']['uuid'], 'u-1')
        self.assertEqual(user['raw']['type'], 'user')
        self.assertEqual(user['raw']['sessionId'], 's-1')
        self.assertEqual(user['raw']['timestamp'], '2026-09-15T08:00:00.000Z')
        self.assertEqual(
            user['raw']['message']['content'][1],
            {'type': 'text', 'text': 'what did the command print?'},
        )
        message = assistant['raw']['message']
        self.assertEqual((message['id'], message['role'], message['model']),
                         ('msg-2', 'assistant', 'claude-opus'))
        self.assertEqual(message['content'], ASSISTANT_RECORD['message']['content'])

    def test_the_prompt_time_is_still_carried(self) -> None:
        (epoch, user), = _replay([USER_RECORD])
        self.assertGreater(epoch, 0)
        self.assertEqual(user['received_at_epoch'], epoch)

    def test_the_transcript_record_itself_is_not_modified(self) -> None:
        records = [copy.deepcopy(USER_RECORD), copy.deepcopy(ASSISTANT_RECORD)]
        _replay(records)
        self.assertEqual(records, [USER_RECORD, ASSISTANT_RECORD])

    def test_the_replay_is_much_smaller(self) -> None:
        full = len(json.dumps([USER_RECORD, ASSISTANT_RECORD]))
        sent = len(json.dumps([event['raw'] for _epoch, event in _replay([USER_RECORD, ASSISTANT_RECORD])]))
        self.assertLess(sent, full / 3)

    def test_records_without_a_message_pass_through(self) -> None:
        record = {'type': 'system', 'subtype': 'notice', 'uuid': 'u-9', 'text': 'hello'}
        (_epoch, event), = _replay([record])
        self.assertEqual(event['raw'], record)


if __name__ == '__main__':
    unittest.main()
