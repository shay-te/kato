"""Tests for the Codex session index — the list behind the adoption picker.

The picker's job is to let an operator recognise a conversation they were
just in, so these tests are mostly about the three things it shows (cwd,
message previews, recency) surviving a store that is never guaranteed to be
tidy: a rollout being written while it is read, a truncated line, a file with
no metadata header at all.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from codex_core_lib.codex_core_lib.session.index import (
    default_sessions_root,
    list_sessions,
)


def _meta(session_id='thread-1', cwd='/work/proj'):
    return json.dumps({
        'type': 'session_meta',
        'payload': {'id': session_id, 'cwd': cwd},
    })


def _user(text):
    return json.dumps({
        'type': 'response_item',
        'payload': {
            'type': 'message', 'role': 'user',
            'content': [{'type': 'input_text', 'text': text}],
        },
    })


def _assistant(text):
    return json.dumps({
        'type': 'response_item',
        'payload': {
            'type': 'message', 'role': 'assistant',
            'content': [{'type': 'output_text', 'text': text}],
        },
    })


class SessionIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _write(self, name, lines, *, subdir='2026/08/30'):
        day = self.root / subdir
        day.mkdir(parents=True, exist_ok=True)
        path = day / name
        path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        return path

    def _list(self, **kwargs):
        return list_sessions(sessions_root=self.root, **kwargs)

    # ----- the happy path ------------------------------------------------

    def test_it_reads_cwd_id_and_both_message_previews(self) -> None:
        self._write('rollout-2026-08-30T10-00-00-thread-1.jsonl', [
            _meta('thread-1', '/work/proj'),
            _user('first question'),
            _assistant('some answer'),
            _user('second question'),
        ])
        rows = self._list()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.agent_session_id, 'thread-1')
        self.assertEqual(row.cwd, '/work/proj')
        self.assertEqual(row.first_user_message, 'first question')
        self.assertEqual(row.last_user_message, 'second question')

    def test_turn_count_counts_only_the_operator_s_turns(self) -> None:
        # Counting the agent's replies would make this a measure of
        # verbosity rather than of conversation depth.
        self._write('rollout-2026-08-30T10-00-00-thread-1.jsonl', [
            _meta(), _user('one'), _assistant('a'), _assistant('b'),
            _user('two'),
        ])
        self.assertEqual(self._list()[0].turn_count, 2)

    def test_rows_come_back_newest_first(self) -> None:
        old = self._write('rollout-2026-08-30T09-00-00-old.jsonl', [_meta('old')])
        new = self._write('rollout-2026-08-30T11-00-00-new.jsonl', [_meta('new')])
        import os
        os.utime(old, (1_000, 1_000))
        os.utime(new, (2_000, 2_000))
        self.assertEqual(
            [r.agent_session_id for r in self._list()], ['new', 'old'],
        )

    def test_it_walks_nested_date_folders(self) -> None:
        self._write('rollout-a-thread-a.jsonl', [_meta('thread-a')],
                    subdir='2026/07/01')
        self._write('rollout-b-thread-b.jsonl', [_meta('thread-b')],
                    subdir='2026/08/30')
        self.assertEqual(len(self._list()), 2)

    # ----- filtering -----------------------------------------------------

    def test_query_matches_the_cwd(self) -> None:
        self._write('rollout-a-thread-a.jsonl', [_meta('thread-a', '/work/alpha')])
        self._write('rollout-b-thread-b.jsonl', [_meta('thread-b', '/work/beta')])
        rows = self._list(query='alpha')
        self.assertEqual([r.agent_session_id for r in rows], ['thread-a'])

    def test_query_matches_message_text_and_ignores_case(self) -> None:
        self._write('rollout-a-thread-a.jsonl', [
            _meta('thread-a'), _user('fix the AUTH bug'),
        ])
        self._write('rollout-b-thread-b.jsonl', [
            _meta('thread-b'), _user('write docs'),
        ])
        rows = self._list(query='auth')
        self.assertEqual([r.agent_session_id for r in rows], ['thread-a'])

    def test_max_results_caps_the_page(self) -> None:
        for i in range(5):
            self._write(f'rollout-x-thread-{i}.jsonl', [_meta(f'thread-{i}')])
        self.assertEqual(len(self._list(max_results=2)), 2)

    # ----- a store that is not tidy --------------------------------------

    def test_a_missing_store_is_empty_not_an_error(self) -> None:
        # A host with no Codex CLI installed simply has nothing to adopt.
        self.assertEqual(
            list_sessions(sessions_root=self.root / 'nope'), [],
        )

    def test_a_truncated_line_is_skipped_not_raised(self) -> None:
        # A rollout being written while it is read ends mid-line.
        self._write('rollout-a-thread-a.jsonl', [
            _meta('thread-a'), _user('kept'), '{"type": "response_i',
        ])
        rows = self._list()
        self.assertEqual(rows[0].first_user_message, 'kept')

    def test_the_id_falls_back_to_the_filename(self) -> None:
        # A rollout truncated at byte zero has no ``session_meta`` to read,
        # but it still has a name — and the CLI puts the thread id there.
        self._write(
            'rollout-2026-08-30T10-00-00-0199a1b2-c3d4-7e8f-9012-3456789abcde'
            '.jsonl',
            [],
        )
        self.assertEqual(
            self._list()[0].agent_session_id,
            '0199a1b2-c3d4-7e8f-9012-3456789abcde',
        )

    def test_the_id_inside_the_file_beats_the_filename(self) -> None:
        # A renamed or copied rollout still knows its own id.
        self._write('rollout-2026-08-30T10-00-00-onthename.jsonl', [
            _meta('real-id'),
        ])
        self.assertEqual(self._list()[0].agent_session_id, 'real-id')

    def test_a_file_with_no_usable_id_is_dropped(self) -> None:
        self._write('rollout-nope.jsonl', [_meta('')])
        self.assertEqual(self._list(), [])

    def test_the_timestamp_is_not_mistaken_for_part_of_the_id(self) -> None:
        # The timestamp is digits and dashes, exactly like the UUID that
        # follows it, so a loose tail match splits inside it and produces
        # ``00-00-<uuid>`` — an id that resolves to no session at all.
        self._write(
            'rollout-2026-08-30T10-00-00-0199a1b2-c3d4-7e8f-9012-3456789abcde'
            '.jsonl',
            [_user('no session_meta in this one')],
        )
        self.assertEqual(
            self._list()[0].agent_session_id,
            '0199a1b2-c3d4-7e8f-9012-3456789abcde',
        )

    def test_non_rollout_files_are_ignored(self) -> None:
        day = self.root / '2026/08/30'
        day.mkdir(parents=True)
        (day / 'notes.txt').write_text('hello', encoding='utf-8')
        self.assertEqual(self._list(), [])

    def test_a_long_preview_is_trimmed_and_whitespace_collapsed(self) -> None:
        self._write('rollout-a-thread-a.jsonl', [
            _meta('thread-a'), _user('a  b\n\nc ' + ('x' * 400)),
        ])
        preview = self._list()[0].first_user_message
        self.assertLessEqual(len(preview), 160)
        self.assertTrue(preview.startswith('a b c'))

    def test_string_content_is_read_as_well_as_block_lists(self) -> None:
        self._write('rollout-a-thread-a.jsonl', [
            _meta('thread-a'),
            json.dumps({
                'type': 'response_item',
                'payload': {
                    'type': 'message', 'role': 'user', 'content': 'plain string',
                },
            }),
        ])
        self.assertEqual(self._list()[0].first_user_message, 'plain string')

    def test_to_dict_exposes_every_field_the_picker_draws(self) -> None:
        self._write('rollout-a-thread-a.jsonl', [_meta('thread-a'), _user('hi')])
        row = self._list()[0].to_dict()
        self.assertEqual(sorted(row), [
            'agent_session_id', 'cwd', 'first_user_message',
            'last_modified_epoch', 'last_user_message', 'turn_count',
        ])


class DefaultSessionsRootTests(unittest.TestCase):
    def test_it_honours_the_cli_s_own_home_variable(self) -> None:
        with mock.patch.dict('os.environ', {'CODEX_HOME': '/custom/home'}):
            self.assertEqual(
                default_sessions_root(), Path('/custom/home/sessions'),
            )

    def test_it_falls_back_to_the_default_home(self) -> None:
        root = default_sessions_root({})
        self.assertEqual(root.name, 'sessions')
        self.assertEqual(root.parent.name, '.codex')


if __name__ == '__main__':
    unittest.main()
