"""Replaying a Grok chat from ``$GROK_HOME/sessions/<cwd>/<id>/updates.jsonl``."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from grok_core_lib.grok_core_lib.session.history import (
    find_session_dir,
    grok_home,
    load_history_events,
    session_update,
)


def _store(updates: list, *, session_id: str = 'sess-1') -> Path:
    home = Path(tempfile.mkdtemp())
    folder = home / 'sessions' / '%2Fwork%2Frepo' / session_id
    folder.mkdir(parents=True)
    (folder / 'updates.jsonl').write_text(
        '\n'.join(json.dumps(line) if not isinstance(line, str) else line for line in updates),
        encoding='utf-8',
    )
    return home


def _chunk(kind: str, text: str, **extra) -> dict:
    return {'sessionUpdate': kind, 'content': {'type': 'text', 'text': text}, **extra}


class LoadHistoryTests(unittest.TestCase):
    def test_user_and_agent_messages_replay_in_the_live_shape(self) -> None:
        home = _store([
            _chunk('user_message_chunk', 'fix the bug', timestamp='2026-11-12T10:00:00Z'),
            {'update': _chunk('agent_message_chunk', 'Looking ')},
            {'params': {'update': _chunk('agent_message_chunk', 'now.')}},
            _chunk('agent_thought_chunk', 'hidden'),
            {'sessionUpdate': 'tool_call', 'title': 'Read'},
            'not json',
            _chunk('user_message_chunk', ''),
        ])
        events = load_history_events('sess-1', home=home)
        self.assertEqual(events[0]['type'], 'user')
        self.assertEqual(events[0]['message']['content'][0]['text'], 'fix the bug')
        self.assertEqual(events[0]['timestamp'], '2026-11-12T10:00:00Z')
        self.assertEqual(events[1]['item'], {'type': 'agent_message', 'text': 'Looking now.'})
        self.assertEqual(len(events), 2)

    def test_the_tail_survives_a_cap(self) -> None:
        home = _store([_chunk('user_message_chunk', f'q{n}') if n % 2 == 0
                       else _chunk('agent_message_chunk', f'a{n}') for n in range(6)])
        self.assertEqual(len(load_history_events('sess-1', home=home, max_events=2)), 2)
        self.assertEqual(load_history_events('sess-1', home=home, max_events=0), [])

    def test_anything_unreadable_answers_empty(self) -> None:
        home = _store([])
        self.assertEqual(load_history_events('', home=home), [])
        self.assertEqual(load_history_events('../escape', home=home), [])
        self.assertEqual(load_history_events('no-such', home=home), [])
        self.assertEqual(load_history_events('sess-1', home=Path(tempfile.mkdtemp())), [])
        (home / 'sessions' / '%2Fwork%2Frepo' / 'sess-1' / 'updates.jsonl').unlink()
        self.assertEqual(load_history_events('sess-1', home=home), [])

    def test_the_home_follows_grok_home(self) -> None:
        self.assertEqual(grok_home({'GROK_HOME': '/custom'}), Path('/custom'))
        self.assertEqual(grok_home({}), Path.home() / '.grok')
        with patch.dict('os.environ', {'GROK_HOME': '/from-env'}):
            self.assertEqual(grok_home(), Path('/from-env'))

    def test_the_newest_folder_wins_when_two_groups_hold_the_id(self) -> None:
        home = _store([], session_id='dup')
        other = home / 'sessions' / 'other-cwd' / 'dup'
        other.mkdir(parents=True)
        self.assertIsNotNone(find_session_dir('dup', home=home))

    def test_an_update_is_found_wherever_the_line_put_it(self) -> None:
        self.assertIsNone(session_update({'nothing': 1}))
        self.assertEqual(session_update({'sessionUpdate': 'x'})['sessionUpdate'], 'x')


if __name__ == '__main__':  # pragma: no cover
    unittest.main()


class StoreErrorTests(unittest.TestCase):
    def test_a_store_that_cannot_be_walked_has_no_session(self) -> None:
        home = _store([])
        with patch.object(Path, 'glob', side_effect=OSError('denied')):
            self.assertIsNone(find_session_dir('sess-1', home=home))

    def test_an_unstattable_folder_sorts_last(self) -> None:
        from grok_core_lib.grok_core_lib.session import history
        self.assertEqual(history._mtime(Path('/no/such/folder')), 0.0)
