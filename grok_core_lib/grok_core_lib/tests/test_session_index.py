"""Listing Grok sessions on disk for the adoption picker."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from grok_core_lib.grok_core_lib.session.index import (
    GrokSessionMetadata,
    default_sessions_root,
    list_sessions,
)


def _session(root: Path, group: str, session_id: str, prompts: list[str], *, summary=None) -> Path:
    folder = root / group / session_id
    folder.mkdir(parents=True)
    lines = []
    for prompt in prompts:
        lines.append({'sessionUpdate': 'user_message_chunk', 'content': {'type': 'text', 'text': prompt}})
        lines.append({'sessionUpdate': 'agent_message_chunk', 'content': {'type': 'text', 'text': 'ok'}})
    (folder / 'updates.jsonl').write_text('\n'.join(json.dumps(line) for line in lines), encoding='utf-8')
    if summary is not None:
        (folder / 'summary.json').write_text(json.dumps(summary), encoding='utf-8')
    return folder


class ListSessionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())

    def test_rows_carry_the_shared_picker_fields(self) -> None:
        _session(self.root, '%2Fwork%2Fapi', 'sess-1', ['first ask', 'second ask'],
                 summary={'info': {'cwd': '/work/api'}})
        rows = list_sessions(sessions_root=self.root)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertIsInstance(row, GrokSessionMetadata)
        self.assertEqual(row.agent_session_id, 'sess-1')
        self.assertEqual(row.cwd, '/work/api')
        self.assertEqual((row.turn_count, row.first_user_message, row.last_user_message),
                         (2, 'first ask', 'second ask'))
        self.assertEqual(row.to_dict()['agent_session_id'], 'sess-1')

    def test_the_cwd_falls_back_to_the_cwd_file_then_the_group_name(self) -> None:
        _session(self.root, '%2Fwork%2Fweb', 'a', ['x'])
        long_group = self.root / 'slug-abc'
        _session(self.root, 'slug-abc', 'b', ['y'], summary={'info': 'broken'})
        (long_group / '.cwd').write_text('/very/long/path\n', encoding='utf-8')
        cwds = {row.agent_session_id: row.cwd for row in list_sessions(sessions_root=self.root)}
        self.assertEqual(cwds, {'a': '/work/web', 'b': '/very/long/path'})

    def test_query_and_cap_narrow_the_list(self) -> None:
        _session(self.root, 'g', 's1', ['auth bug'])
        _session(self.root, 'g', 's2', ['billing'])
        self.assertEqual([r.agent_session_id for r in list_sessions(sessions_root=self.root, query='AUTH')], ['s1'])
        self.assertEqual(len(list_sessions(sessions_root=self.root, max_results=1)), 1)

    def test_empty_and_missing_stores_list_nothing(self) -> None:
        self.assertEqual(list_sessions(sessions_root=self.root / 'missing'), [])
        (self.root / 'g' / 'empty').mkdir(parents=True)
        self.assertEqual(list_sessions(sessions_root=self.root), [])

    def test_the_default_root_follows_grok_home(self) -> None:
        self.assertEqual(default_sessions_root({'GROK_HOME': '/h'}), Path('/h/sessions'))
        with patch.dict('os.environ', {'GROK_HOME': str(self.root)}):
            self.assertEqual(list_sessions(), [])


if __name__ == '__main__':  # pragma: no cover
    unittest.main()


class StoreErrorTests(unittest.TestCase):
    """A store that cannot be read is an empty list or a blank field, never an error."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())

    def test_a_store_that_cannot_be_walked_lists_nothing(self) -> None:
        with patch.object(Path, 'glob', side_effect=OSError('denied')):
            self.assertEqual(list_sessions(sessions_root=self.root), [])

    def test_an_unreadable_log_still_lists_the_session(self) -> None:
        from grok_core_lib.grok_core_lib.session import index
        folder = _session(self.root, 'g', 's1', ['a long ask'])
        self.assertEqual(index._user_messages(folder / 'missing.jsonl'), ('', '', 0))

    def test_a_long_log_is_only_scanned_up_to_the_preview_cap(self) -> None:
        from grok_core_lib.grok_core_lib.session import index
        _session(self.root, 'g', 's1', ['early ask'] + ['x' * 4000] * 200)
        with patch.object(index, 'MAX_PREVIEW_SCAN_BYTES', 300):
            row = list_sessions(sessions_root=self.root)[0]
        self.assertEqual(row.first_user_message, 'early ask')
        self.assertEqual(row.turn_count, 1)

    def test_a_folder_with_no_readable_files_has_no_mtime(self) -> None:
        from grok_core_lib.grok_core_lib.session import index
        self.assertEqual(index._last_modified(self.root / 'gone'), 0.0)
        (self.root / 'bare').mkdir()
        self.assertGreater(index._last_modified(self.root / 'bare'), 0.0)


class EmptyPromptTests(unittest.TestCase):
    def test_an_empty_user_message_is_not_a_turn(self) -> None:
        root = Path(tempfile.mkdtemp())
        _session(root, 'g', 's1', ['', 'real ask'])
        row = list_sessions(sessions_root=root)[0]
        self.assertEqual((row.turn_count, row.first_user_message), (1, 'real ask'))
