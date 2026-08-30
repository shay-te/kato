"""Operator-given names for chat sessions.

A chat had no name of its own — the list labelled it with its first user
message, which is a reasonable guess and a poor name. This is where a real
one lives.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from kato_core_lib.helpers.chat_name_store import (
    MAX_NAME_LENGTH,
    chat_name,
    forget_chat_names,
    read_chat_names,
    set_chat_name,
)


class ChatNameStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self._path = str(Path(self._td.name) / 'chat_names.json')
        patcher = unittest.mock.patch.dict(
            os.environ, {'KATO_CHAT_NAMES_PATH': self._path},
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_empty_when_no_file(self) -> None:
        self.assertEqual(read_chat_names(), {})
        self.assertEqual(chat_name('abc'), '')

    def test_round_trip(self) -> None:
        set_chat_name('abc', 'The flaky test hunt')
        self.assertEqual(chat_name('abc'), 'The flaky test hunt')
        self.assertTrue(Path(self._path).is_file())

    def test_an_empty_name_CLEARS_rather_than_storing_blank(self) -> None:
        # Rename and un-rename are one operation; the list then falls back to
        # its derived label, and the file does not collect tombstones.
        set_chat_name('abc', 'temporary')
        set_chat_name('abc', '')
        self.assertEqual(chat_name('abc'), '')
        self.assertEqual(json.loads(Path(self._path).read_text()), {})

    def test_whitespace_only_also_clears(self) -> None:
        set_chat_name('abc', 'temporary')
        set_chat_name('abc', '   \n  ')
        self.assertEqual(chat_name('abc'), '')

    def test_newlines_are_collapsed(self) -> None:
        # The name renders in a single-line row; storing what will actually be
        # shown beats truncating it at display time.
        stored = set_chat_name('abc', 'first line\n\nsecond   line')
        self.assertEqual(stored, 'first line second line')

    def test_a_long_name_is_capped(self) -> None:
        stored = set_chat_name('abc', 'x' * (MAX_NAME_LENGTH + 50))
        self.assertEqual(len(stored), MAX_NAME_LENGTH)

    def test_names_are_per_chat_not_per_task(self) -> None:
        # A task has many chats over its life, so a task-keyed name would
        # follow whichever chat was active and re-label one nobody touched.
        set_chat_name('chat-1', 'first conversation')
        set_chat_name('chat-2', 'second conversation')
        self.assertEqual(chat_name('chat-1'), 'first conversation')
        self.assertEqual(chat_name('chat-2'), 'second conversation')

    def test_a_blank_chat_id_is_ignored(self) -> None:
        set_chat_name('', 'nowhere')
        set_chat_name('   ', 'nowhere')
        self.assertEqual(read_chat_names(), {})

    def test_writing_the_same_name_twice_does_not_touch_disk(self) -> None:
        set_chat_name('abc', 'stable')
        mtime = Path(self._path).stat().st_mtime_ns
        set_chat_name('abc', 'stable')
        self.assertEqual(Path(self._path).stat().st_mtime_ns, mtime)

    def test_an_unreadable_file_reads_as_empty(self) -> None:
        # A corrupt file must not brick the chats menu; the next write repairs.
        Path(self._path).write_text('{not json', encoding='utf-8')
        self.assertEqual(read_chat_names(), {})
        set_chat_name('abc', 'recovered')
        self.assertEqual(chat_name('abc'), 'recovered')

    def test_a_wrong_shape_reads_as_empty(self) -> None:
        Path(self._path).write_text('["abc"]', encoding='utf-8')
        self.assertEqual(read_chat_names(), {})

    def test_forget_drops_only_the_named_chats(self) -> None:
        set_chat_name('keep', 'kept')
        set_chat_name('drop-1', 'gone')
        set_chat_name('drop-2', 'gone too')

        forget_chat_names(['drop-1', 'drop-2', 'never-existed'])

        self.assertEqual(read_chat_names(), {'keep': 'kept'})

    def test_forget_with_nothing_to_do_is_a_no_op(self) -> None:
        set_chat_name('keep', 'kept')
        mtime = Path(self._path).stat().st_mtime_ns
        forget_chat_names([])
        forget_chat_names(['never-existed'])
        self.assertEqual(Path(self._path).stat().st_mtime_ns, mtime)


if __name__ == '__main__':
    unittest.main()
