"""Tests for the crossed-session-id repair tool.

This tool edits the operator's live state directory, so the cases that matter
most are the ones where it must NOT delete: the orientation a fresh leak
actually produces, and any record whose true owner cannot be established.

An earlier version assumed the ACTIVE copy was always the genuine one. That is
true only after the operator has switched back; on a fresh leak it is exactly
inverted, and the tool deleted the real conversation along with its
``previous_session_ids``. Both orientations are pinned below.
"""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.repair_crossed_session_ids import (
    backends_holding,
    crossed_ids,
    main,
    strip_id_from,
)

CLAUDE_ID = 'claude-id-1'


def _fresh_leak():
    """agent_backend=codex holding a CLAUDE id — the ACTIVE copy is bogus."""
    return {
        'task_id': 'T1',
        'agent_backend': 'codex',
        'agent_session_id': CLAUDE_ID,
        'chats_by_backend': {
            'claude': {
                'agent_session_id': CLAUDE_ID,
                'previous_session_ids': ['older-1', 'older-2'],
            },
        },
    }


def _after_switch_back():
    """agent_backend=claude, bogus copy PARKED under codex."""
    return {
        'task_id': 'T1',
        'agent_backend': 'claude',
        'agent_session_id': CLAUDE_ID,
        'chats_by_backend': {'codex': {'agent_session_id': CLAUDE_ID}},
    }


class DetectionTests(unittest.TestCase):
    def test_it_sees_both_orientations(self) -> None:
        self.assertEqual(crossed_ids(_fresh_leak()), [CLAUDE_ID])
        self.assertEqual(crossed_ids(_after_switch_back()), [CLAUDE_ID])

    def test_holders_include_the_active_backend(self) -> None:
        # The active entry is the bogus one as often as a parked entry is, so
        # leaving it out of the candidates is what made the old version
        # delete the wrong side.
        self.assertEqual(
            backends_holding(_fresh_leak(), CLAUDE_ID), ['claude', 'codex'],
        )

    def test_a_genuine_pair_of_distinct_chats_is_not_flagged(self) -> None:
        record = {
            'agent_backend': 'claude',
            'agent_session_id': CLAUDE_ID,
            'chats_by_backend': {'codex': {'agent_session_id': 'codex-9'}},
        }
        self.assertEqual(crossed_ids(record), [])

    def test_a_clean_record_is_not_flagged(self) -> None:
        self.assertEqual(crossed_ids({
            'agent_backend': 'claude', 'agent_session_id': CLAUDE_ID,
            'chats_by_backend': {},
        }), [])

    def test_empty_ids_never_count_as_crossed(self) -> None:
        # Two backends both holding '' is not a collision.
        record = {
            'agent_backend': 'claude', 'agent_session_id': '',
            'chats_by_backend': {'codex': {'agent_session_id': ''}},
        }
        self.assertEqual(crossed_ids(record), [])

    def test_a_malformed_chats_map_does_not_raise(self) -> None:
        self.assertEqual(crossed_ids({
            'agent_backend': 'claude', 'agent_session_id': CLAUDE_ID,
            'chats_by_backend': 'nope',
        }), [])


class StripTests(unittest.TestCase):
    def test_a_parked_duplicate_is_dropped_whole(self) -> None:
        record = strip_id_from(_after_switch_back(), CLAUDE_ID, ['codex'])
        self.assertEqual(record['chats_by_backend'], {})
        self.assertEqual(record['agent_session_id'], CLAUDE_ID)

    def test_an_active_duplicate_is_cleared_not_dropped(self) -> None:
        # The record must keep a backend; clearing the id is the truth —
        # that agent never had this conversation.
        record = strip_id_from(_fresh_leak(), CLAUDE_ID, ['codex'])
        self.assertEqual(record['agent_backend'], 'codex')
        self.assertEqual(record['agent_session_id'], '')
        # The genuine Claude chat and its history are untouched.
        self.assertEqual(
            record['chats_by_backend']['claude']['previous_session_ids'],
            ['older-1', 'older-2'],
        )


class RepairRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _write(self, record, name='t.json'):
        path = self.root / name
        path.write_text(json.dumps(record), encoding='utf-8')
        return path

    def _run(self, *extra, owner='claude'):
        with mock.patch(
            'scripts.repair_crossed_session_ids._store_owner',
            return_value=owner,
        ):
            return main(['--sessions-dir', str(self.root), *extra])

    def _read(self, path):
        return json.loads(path.read_text(encoding='utf-8'))

    def test_the_fresh_leak_keeps_the_real_chat_and_clears_the_bogus_id(self):
        # THE REGRESSION. The old version deleted chats_by_backend['claude']
        # — the operator's real conversation, with its previous_session_ids —
        # and kept the corrupt active id.
        path = self._write(_fresh_leak())
        self._run('--apply')
        after = self._read(path)
        self.assertEqual(
            after['chats_by_backend']['claude']['previous_session_ids'],
            ['older-1', 'older-2'],
        )
        self.assertEqual(after['agent_session_id'], '')

    def test_after_switch_back_the_parked_duplicate_goes(self) -> None:
        path = self._write(_after_switch_back())
        self._run('--apply')
        after = self._read(path)
        self.assertNotIn('codex', after['chats_by_backend'])
        self.assertEqual(after['agent_session_id'], CLAUDE_ID)

    def test_an_unresolvable_id_is_skipped_not_deleted(self) -> None:
        # No store claims the id (pruned, or adopted from another machine).
        # Refusing to act beats deleting the wrong conversation.
        path = self._write(_fresh_leak())
        before = self._read(path)
        self._run('--apply', owner='')
        self.assertEqual(self._read(path), before)
        self.assertFalse(path.with_suffix('.json.bak').exists())

    def test_a_dry_run_changes_nothing(self) -> None:
        path = self._write(_after_switch_back())
        before = path.read_text(encoding='utf-8')
        self._run()
        self.assertEqual(path.read_text(encoding='utf-8'), before)

    def _run_capturing(self, *extra, owner='claude'):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            self._run(*extra, owner=owner)
        return buffer.getvalue()

    def test_a_dry_run_REPORTS_what_it_found(self) -> None:
        # The counter only advanced on the --apply path, so a dry run
        # printed "no crossed session ids found." immediately below a list
        # of crossed session ids. Against the operator's real records that
        # summary said "nothing to do" three times over, which is exactly
        # the line that stops anyone re-running with --apply.
        self._write(_after_switch_back())
        output = self._run_capturing()
        self.assertNotIn('no crossed session ids found', output)
        self.assertIn('1 record(s) would be repaired', output)
        self.assertIn('--apply', output)

    def test_a_dry_run_over_clean_records_still_says_nothing_found(self) -> None:
        # The other half: the reassuring message has to stay reachable, or
        # the fix above just inverts the lie.
        self._write({'agent_backend': 'claude', 'agent_session_id': CLAUDE_ID})
        output = self._run_capturing()
        self.assertIn('no crossed session ids found', output)

    def test_apply_reports_what_it_actually_wrote(self) -> None:
        self._write(_after_switch_back())
        output = self._run_capturing('--apply')
        self.assertIn('repaired 1 record(s)', output)

    def test_apply_backs_the_file_up_first(self) -> None:
        path = self._write(_after_switch_back())
        self._run('--apply')
        self.assertIn(
            'codex',
            self._read(path.with_suffix('.json.bak'))['chats_by_backend'],
        )

    def test_an_unrelated_parked_chat_survives(self) -> None:
        record = _after_switch_back()
        record['chats_by_backend']['openhands'] = {'agent_session_id': 'oh-7'}
        path = self._write(record)
        self._run('--apply')
        after = self._read(path)
        self.assertEqual(
            after['chats_by_backend']['openhands']['agent_session_id'], 'oh-7',
        )

    def test_a_clean_record_is_not_rewritten(self) -> None:
        path = self._write({
            'agent_backend': 'claude', 'agent_session_id': CLAUDE_ID,
            'chats_by_backend': {},
        })
        before = path.read_text(encoding='utf-8')
        self._run('--apply')
        self.assertEqual(path.read_text(encoding='utf-8'), before)
        self.assertFalse(path.with_suffix('.json.bak').exists())

    def test_an_unreadable_record_is_skipped_not_fatal(self) -> None:
        (self.root / 'broken.json').write_text('{not json', encoding='utf-8')
        self._write(_after_switch_back(), name='good.json')
        self.assertEqual(self._run('--apply'), 0)

    def test_a_missing_directory_is_not_an_error(self) -> None:
        self.assertEqual(main(['--sessions-dir', str(self.root / 'nope')]), 0)


if __name__ == '__main__':
    unittest.main()
