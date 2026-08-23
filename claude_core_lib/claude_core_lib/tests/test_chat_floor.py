"""Recovering a chat's cost floor from its own transcript.

Without this, a chat that was already running when the cost indicator arrived
has no floor to measure against — and the tempting shortcut (treat its current
size as the floor) reports the most expensive conversation on the machine as
1.0x, i.e. a green light on the one chat that needs restarting. The transcript
already holds the honest answer: the first assistant turn happened when the
context WAS new.

Verified against a real 56 MB transcript: 30,788 tokens, read from its first
200 lines.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from claude_core_lib.claude_core_lib.helpers import chat_floor


def _turn(**usage):
    return json.dumps({'type': 'assistant', 'message': {'usage': usage}})


class FirstTurnTokensTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix='chat-floor-')
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.log = self.root / 'sess.jsonl'

    def test_reads_the_first_assistant_turn(self) -> None:
        self.log.write_text('\n'.join([
            json.dumps({'type': 'summary', 'summary': 'x'}),
            json.dumps({'type': 'user', 'message': {'content': 'hi'}}),
            _turn(input_tokens=12, cache_read_input_tokens=30_000,
                  cache_creation_input_tokens=776, output_tokens=999),
            _turn(input_tokens=5, cache_read_input_tokens=490_000),
        ]) + '\n', encoding='utf-8')
        # Everything the model READ: 12 + 30,000 + 776. Output is what the
        # turn produced, not what it cost to send, so it is excluded.
        self.assertEqual(chat_floor.first_turn_tokens(self.log), 30_788)

    def test_output_only_turns_are_skipped(self) -> None:
        self.log.write_text('\n'.join([
            _turn(output_tokens=120),
            _turn(input_tokens=40_000),
        ]) + '\n', encoding='utf-8')
        self.assertEqual(chat_floor.first_turn_tokens(self.log), 40_000)

    def test_it_gives_up_rather_than_reading_a_huge_log(self) -> None:
        # Real transcripts reach hundreds of megabytes; the first turn is at
        # the top of an append-ordered file or it is not worth finding.
        filler = json.dumps({'type': 'user', 'message': {'content': 'x'}})
        lines = [filler] * (chat_floor._MAX_LINES + 5) + [_turn(input_tokens=40_000)]
        self.log.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        self.assertEqual(chat_floor.first_turn_tokens(self.log), 0)

    def test_corrupt_lines_are_stepped_over(self) -> None:
        self.log.write_text('\n'.join([
            'not json at all',
            json.dumps(['a list, not a record']),
            json.dumps({'message': 'not a dict'}),
            json.dumps({'message': {'usage': 'not a dict'}}),
            _turn(input_tokens=25_000),
        ]) + '\n', encoding='utf-8')
        self.assertEqual(chat_floor.first_turn_tokens(self.log), 25_000)

    def test_junk_usage_values_do_not_crash(self) -> None:
        self.log.write_text(
            _turn(input_tokens='lots', cache_read_input_tokens=None) + '\n'
            + _turn(input_tokens=9_000) + '\n', encoding='utf-8')
        self.assertEqual(chat_floor.first_turn_tokens(self.log), 9_000)

    def test_a_missing_file_is_zero_not_an_error(self) -> None:
        self.assertEqual(chat_floor.first_turn_tokens(self.root / 'nope.jsonl'), 0)


class ChatFloorLookupTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix='chat-floor-lookup-')
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.cwd = self.root / 'workspace'
        self.cwd.mkdir()
        self.sessions = self.root / 'sessions'
        self.sessions.mkdir()
        patcher = patch.dict(os.environ, {'CLAUDE_SESSIONS_ROOT': str(self.sessions)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _write_transcript(self, session_id: str, tokens: int) -> None:
        from claude_core_lib.claude_core_lib.session.index import (
            claude_project_dir_for_cwd,
        )
        directory = claude_project_dir_for_cwd(str(self.cwd))
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f'{session_id}.jsonl').write_text(
            _turn(input_tokens=tokens) + '\n', encoding='utf-8',
        )

    def test_finds_the_transcript_for_a_session(self) -> None:
        self._write_transcript('sess-9', 31_000)
        self.assertEqual(chat_floor.chat_floor_tokens('sess-9', str(self.cwd)), 31_000)

    def test_an_unknown_session_is_zero(self) -> None:
        self.assertEqual(chat_floor.chat_floor_tokens('nope', str(self.cwd)), 0)

    def test_blank_inputs_are_zero(self) -> None:
        self.assertEqual(chat_floor.chat_floor_tokens('', str(self.cwd)), 0)
        self.assertEqual(chat_floor.chat_floor_tokens('sess-9', ''), 0)


if __name__ == '__main__':
    unittest.main()
