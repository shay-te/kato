"""A Codex chat must survive a restart of the process hosting it.

The live event log lived only in the session object's memory, so a chat
survived a page reload but not a restart — the operator came back to an empty
tab. The CLI writes every turn to a rollout transcript; this reads it back.

Events come back in the SAME wire shape the live stream emits, so a replayed
conversation renders through exactly the path a live one does. A second
shape would mean a second renderer, and the two would drift.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from codex_core_lib.codex_core_lib.session.history import (
    codex_home,
    find_rollout_path,
    load_history_events,
)


def _rollout(home: Path, thread_id: str, records: list[dict]) -> Path:
    day = home / 'sessions' / '2026' / '05' / '25'
    day.mkdir(parents=True, exist_ok=True)
    path = day / f'rollout-2026-05-25T09-19-32-{thread_id}.jsonl'
    with path.open('w', encoding='utf-8') as handle:
        for record in records:
            handle.write(json.dumps(record) + '\n')
    return path


def _message(role: str, text: str) -> dict:
    key = 'input_text' if role == 'user' else 'output_text'
    return {
        'type': 'response_item',
        'payload': {
            'type': 'message', 'role': role,
            'content': [{'type': key, 'text': text}],
        },
    }


class RolloutLookupTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix='codex-history-')
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)

    def test_it_finds_the_rollout_by_thread_id(self) -> None:
        path = _rollout(self.home, 'abc-123', [_message('user', 'hi')])
        self.assertEqual(find_rollout_path('abc-123', home=self.home), path)

    def test_an_unknown_thread_has_no_rollout(self) -> None:
        _rollout(self.home, 'abc-123', [])
        self.assertIsNone(find_rollout_path('nope', home=self.home))

    def test_an_empty_id_never_guesses(self) -> None:
        _rollout(self.home, 'abc-123', [])
        self.assertIsNone(find_rollout_path('', home=self.home))

    def test_a_missing_sessions_directory_is_not_an_error(self) -> None:
        self.assertIsNone(find_rollout_path('abc', home=self.home))

    def test_the_NEWEST_file_wins_for_a_resumed_chat(self) -> None:
        import os
        import time
        old = _rollout(self.home, 'abc', [_message('user', 'first')])
        day2 = self.home / 'sessions' / '2026' / '05' / '26'
        day2.mkdir(parents=True)
        new = day2 / 'rollout-2026-05-26T10-00-00-abc.jsonl'
        new.write_text(json.dumps(_message('user', 'second')) + '\n')
        os.utime(old, (time.time() - 600, time.time() - 600))
        self.assertEqual(find_rollout_path('abc', home=self.home), new)


class HistoryShapeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix='codex-history-shape-')
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)

    def _load(self, records):
        _rollout(self.home, 'abc', records)
        return load_history_events('abc', home=self.home)

    def test_a_prompt_replays_in_the_live_user_shape(self) -> None:
        events = self._load([_message('user', 'review my changes')])
        self.assertEqual(events, [{
            'type': 'user',
            'message': {'content': [{'type': 'text', 'text': 'review my changes'}]},
        }])

    def test_a_reply_replays_in_the_live_item_shape(self) -> None:
        events = self._load([_message('assistant', 'Looks good.')])
        self.assertEqual(events, [{
            'type': 'item.completed',
            'item': {'type': 'agent_message', 'text': 'Looks good.'},
        }])

    def test_the_conversation_keeps_its_order(self) -> None:
        events = self._load([
            _message('user', 'one'),
            _message('assistant', 'two'),
            _message('user', 'three'),
        ])
        self.assertEqual(
            [e['type'] for e in events],
            ['user', 'item.completed', 'user'],
        )

    def test_the_injected_preamble_is_not_shown(self) -> None:
        # ``developer`` is the permissions / AGENTS.md block the CLI injects —
        # machinery the operator neither wrote nor asked to read.
        events = self._load([
            _message('developer', '<permissions instructions>'),
            _message('user', 'hi'),
        ])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['type'], 'user')

    def test_tool_calls_and_reasoning_are_not_shown(self) -> None:
        events = self._load([
            {'type': 'response_item', 'payload': {'type': 'reasoning'}},
            {'type': 'response_item', 'payload': {'type': 'function_call'}},
            {'type': 'event_msg', 'payload': {'type': 'task_started'}},
            _message('user', 'hi'),
        ])
        self.assertEqual(len(events), 1)

    def test_an_empty_message_is_dropped(self) -> None:
        self.assertEqual(self._load([_message('user', '   ')]), [])

    def test_multi_part_content_is_joined(self) -> None:
        events = self._load([{
            'type': 'response_item',
            'payload': {
                'type': 'message', 'role': 'assistant',
                'content': [
                    {'type': 'output_text', 'text': 'line one'},
                    {'type': 'output_text', 'text': 'line two'},
                ],
            },
        }])
        self.assertEqual(events[0]['item']['text'], 'line one\nline two')


class DegradesQuietlyTests(unittest.TestCase):
    """A chat that renders empty beats one that fails to open."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix='codex-history-bad-')
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)

    def test_a_truncated_line_does_not_lose_the_rest(self) -> None:
        day = self.home / 'sessions' / '2026' / '05' / '25'
        day.mkdir(parents=True)
        path = day / 'rollout-2026-05-25T09-19-32-abc.jsonl'
        path.write_text(
            '{"type": "response_item", "payload": {"type": "mess\n'
            + json.dumps(_message('user', 'survived')) + '\n',
            encoding='utf-8',
        )
        events = load_history_events('abc', home=self.home)
        self.assertEqual(len(events), 1)

    def test_a_missing_rollout_yields_nothing(self) -> None:
        self.assertEqual(load_history_events('gone', home=self.home), [])

    def test_a_very_long_chat_keeps_its_TAIL(self) -> None:
        # The recent exchange is what the operator was reading.
        _rollout(self.home, 'abc', [
            _message('user', str(n)) for n in range(10)
        ])
        events = load_history_events('abc', home=self.home, max_events=3)
        texts = [e['message']['content'][0]['text'] for e in events]
        self.assertEqual(texts, ['7', '8', '9'])


class CodexHomeTests(unittest.TestCase):
    def test_it_honours_CODEX_HOME(self) -> None:
        self.assertEqual(
            codex_home({'CODEX_HOME': '/custom/.codex'}), Path('/custom/.codex')
        )

    def test_it_falls_back_to_the_default(self) -> None:
        self.assertEqual(codex_home({}), Path.home() / '.codex')


if __name__ == '__main__':
    unittest.main()
