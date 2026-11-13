"""Listing OpenCode sessions for the adoption picker, through ``session list``."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from opencode_core_lib.opencode_core_lib.session import index
from opencode_core_lib.opencode_core_lib.session.index import (
    OpenCodeSessionMetadata,
    default_sessions_root,
    list_sessions,
    rows_from_listing,
)

_LISTING = json.dumps([
    {'id': 'ses_old', 'title': 'Old work', 'directory': '/work/api', 'time': {'updated': 1_000_000}},
    {'id': 'ses_new', 'title': 'Auth bug', 'location': {'directory': '/work/web'},
     'time': {'created': 5_000_000}},
    {'id': '', 'title': 'no id'},
    'not a session',
    {'id': 'ses_bad_time', 'time': 'never'},
])


class ListSessionsTests(unittest.TestCase):
    def test_rows_carry_the_shared_picker_fields_newest_first(self) -> None:
        with patch.object(index, 'run_store_command', return_value=_LISTING) as run:
            rows = list_sessions(max_results=10)
        self.assertEqual([row.agent_session_id for row in rows], ['ses_new', 'ses_old', 'ses_bad_time'])
        self.assertIsInstance(rows[0], OpenCodeSessionMetadata)
        self.assertEqual((rows[0].cwd, rows[0].first_user_message, rows[0].turn_count),
                         ('/work/web', 'Auth bug', 0))
        self.assertEqual(rows[0].last_modified_epoch, 5000.0)
        self.assertEqual(run.call_args.args[0][-2:], ['--max-count', '10'])
        self.assertEqual(rows[0].to_dict()['agent_session_id'], 'ses_new')

    def test_a_query_narrows_the_list(self) -> None:
        with patch.object(index, 'run_store_command', return_value=_LISTING):
            self.assertEqual([r.agent_session_id for r in list_sessions(query='auth')], ['ses_new'])

    def test_a_wrapped_listing_and_unreadable_output(self) -> None:
        wrapped = json.dumps({'sessions': [{'id': 'ses_1', 'title': 't'}]})
        self.assertEqual([r.agent_session_id for r in rows_from_listing(wrapped)], ['ses_1'])
        for text in ('', 'nope', '{"sessions": 3}'):
            self.assertEqual(rows_from_listing(text), [])
        with patch.object(index, 'run_store_command', return_value=''):
            self.assertEqual(list_sessions(max_results=0, sessions_root=Path('/x')), [])

    def test_the_default_root_is_opencode_s_data_directory(self) -> None:
        self.assertEqual(default_sessions_root({'XDG_DATA_HOME': '/data'}), Path('/data/opencode'))
        self.assertEqual(default_sessions_root({}), Path.home() / '.local' / 'share' / 'opencode')


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
