"""Tests for the server-side composer-draft store (.kato-prompts.json)."""
from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kato_webserver.prompt_draft_store import (
    DRAFT_FILENAME,
    clear_draft,
    draft_path,
    read_draft,
    write_draft,
)

_IMG = {'media_type': 'image/png', 'data': 'AAAA'}


class PromptDraftStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ws = Path(self._tmp.name)

    def test_read_is_empty_when_no_file(self) -> None:
        self.assertEqual(read_draft(self.ws), {'text': '', 'images': []})

    def test_write_then_read_round_trips_text_and_images(self) -> None:
        write_draft(self.ws, 'fix the bug', [_IMG])
        self.assertEqual(
            read_draft(self.ws), {'text': 'fix the bug', 'images': [_IMG]},
        )

    def test_images_only_no_text_persists(self) -> None:
        write_draft(self.ws, '', [_IMG])
        self.assertEqual(read_draft(self.ws), {'text': '', 'images': [_IMG]})

    def test_blank_text_no_images_deletes_the_file(self) -> None:
        write_draft(self.ws, 'hi', [])
        self.assertTrue(draft_path(self.ws).is_file())
        write_draft(self.ws, '   ', [])  # whitespace-only + no images → cleared
        self.assertFalse(draft_path(self.ws).is_file())
        self.assertEqual(read_draft(self.ws), {'text': '', 'images': []})

    def test_clear_removes_file_and_is_idempotent(self) -> None:
        write_draft(self.ws, 'hi', [])
        clear_draft(self.ws)
        self.assertFalse(draft_path(self.ws).is_file())
        clear_draft(self.ws)  # no-op, no raise

    def test_write_skips_when_workspace_dir_absent(self) -> None:
        # Don't create a workspace folder just for a draft (would look errored).
        missing = self.ws / 'no-such-workspace'
        write_draft(missing, 'hi', [_IMG])
        self.assertFalse((missing / DRAFT_FILENAME).exists())
        self.assertEqual(read_draft(missing), {'text': '', 'images': []})

    def test_malformed_images_are_filtered_out(self) -> None:
        write_draft(self.ws, 'hi', [_IMG, {'media_type': '', 'data': ''}, None, 'bad'])
        self.assertEqual(read_draft(self.ws)['images'], [_IMG])

    def test_read_corrupt_json_is_empty(self) -> None:
        draft_path(self.ws).write_text('not json{', encoding='utf-8')
        self.assertEqual(read_draft(self.ws), {'text': '', 'images': []})

    def test_read_non_dict_payload_is_empty(self) -> None:
        draft_path(self.ws).write_text('[1, 2, 3]', encoding='utf-8')
        self.assertEqual(read_draft(self.ws), {'text': '', 'images': []})

    def test_read_non_list_images_field_is_empty(self) -> None:
        draft_path(self.ws).write_text('{"text": "hi", "images": "nope"}', encoding='utf-8')
        self.assertEqual(read_draft(self.ws), {'text': 'hi', 'images': []})

    def test_read_permission_denied_is_empty_not_crash(self) -> None:
        if hasattr(os, 'geteuid') and os.geteuid() == 0:
            self.skipTest('root bypasses permission checks')
        write_draft(self.ws, 'secret', [])
        os.chmod(self.ws, 0o000)  # stat-ing the file inside now raises
        self.addCleanup(lambda: os.chmod(self.ws, stat.S_IRWXU))
        self.assertEqual(read_draft(self.ws), {'text': '', 'images': []})

    def test_clear_swallows_unlink_oserror(self) -> None:
        write_draft(self.ws, 'hi', [])
        with patch('pathlib.Path.unlink', side_effect=OSError('locked')):
            clear_draft(self.ws)  # must not raise


if __name__ == '__main__':
    unittest.main()
