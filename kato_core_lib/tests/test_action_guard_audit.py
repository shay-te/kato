import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from kato_core_lib.helpers import action_guard_audit as audit
from kato_core_lib.helpers import hash_chain_log


class HashChainLogTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / 'log.jsonl'

    def test_append_and_read_round_trip(self):
        hash_chain_log.append_chained(self.path, {'n': 1})
        hash_chain_log.append_chained(self.path, {'n': 2})
        entries = hash_chain_log.read_entries(self.path)
        self.assertEqual([e['n'] for e in entries], [1, 2])

    def test_first_entry_links_to_genesis(self):
        written = hash_chain_log.append_chained(self.path, {'n': 1})
        self.assertEqual(written['prev_hash'], hash_chain_log.GENESIS_HASH)

    def test_chain_links_each_line_to_previous(self):
        hash_chain_log.append_chained(self.path, {'n': 1})
        hash_chain_log.append_chained(self.path, {'n': 2})
        ok, bad = hash_chain_log.verify_chain(self.path)
        self.assertTrue(ok)
        self.assertEqual(bad, -1)

    def test_tampering_a_middle_line_breaks_the_chain(self):
        for n in range(3):
            hash_chain_log.append_chained(self.path, {'n': n})
        lines = self.path.read_text().splitlines()
        edited = json.loads(lines[0])
        edited['n'] = 999
        lines[0] = json.dumps(edited, sort_keys=True)
        self.path.write_text('\n'.join(lines) + '\n')
        ok, bad = hash_chain_log.verify_chain(self.path)
        self.assertFalse(ok)
        self.assertEqual(bad, 1)  # line 0 edited → line 1's prev_hash mismatches

    def test_read_limit_returns_tail(self):
        for n in range(5):
            hash_chain_log.append_chained(self.path, {'n': n})
        entries = hash_chain_log.read_entries(self.path, limit=2)
        self.assertEqual([e['n'] for e in entries], [3, 4])

    def test_missing_log_is_intact_and_empty(self):
        missing = Path(self.dir.name) / 'nope.jsonl'
        self.assertEqual(hash_chain_log.read_entries(missing), [])
        self.assertEqual(hash_chain_log.verify_chain(missing), (True, -1))

    def test_malformed_line_breaks_verification(self):
        self.path.write_text('not json\n')
        ok, bad = hash_chain_log.verify_chain(self.path)
        self.assertFalse(ok)
        self.assertEqual(bad, 0)


class ActionGuardAuditTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / 'action-guard-audit.log'

    def _record(self, **kw):
        defaults = {
            'task_id': 'UNA-1', 'category': 'credential_read', 'decision': 'block',
            'command': 'cat /Users/dev/.ssh/id_rsa', 'rule_id': 'cred.ssh',
            'request_id': 'req-1', 'answered_by': 'shay.te@gmail.com',
            'audit_log_path': self.path,
            'now': datetime(2026, 6, 15, 7, 30, tzinfo=timezone.utc),
        }
        defaults.update(kw)
        return audit.record_action_guard_decision(**defaults)

    def test_records_decision_with_metadata(self):
        entry = self._record()
        self.assertEqual(entry['task_id'], 'UNA-1')
        self.assertEqual(entry['category'], 'credential_read')
        self.assertEqual(entry['decision'], 'block')
        self.assertEqual(entry['answered_by'], 'shay.te@gmail.com')
        self.assertEqual(entry['event'], 'action_guard_decision')

    def test_raw_command_is_not_stored_only_digest_and_preview(self):
        entry = self._record(command='cat /Users/secretuser/.ssh/id_rsa')
        self.assertTrue(entry['command_digest'].startswith('sha256:'))
        # The actual username is collapsed; the raw absolute path is absent.
        self.assertNotIn('secretuser', json.dumps(entry))
        self.assertIn('~/.ssh/id_rsa', entry['command_preview'])

    def test_preview_truncates_long_commands(self):
        entry = self._record(command='echo ' + 'x' * 500)
        self.assertLessEqual(len(entry['command_preview']), 121)
        self.assertTrue(entry['command_preview'].endswith('…'))

    def test_entries_are_chained_and_verifiable(self):
        self._record(decision='block')
        self._record(decision='ask_approved')
        ok, bad = audit.verify_action_guard_audit(audit_log_path=self.path)
        self.assertTrue(ok)
        self.assertEqual(bad, -1)
        rows = audit.read_action_guard_audit(audit_log_path=self.path)
        self.assertEqual([r['decision'] for r in rows], ['block', 'ask_approved'])

    def test_record_is_best_effort_on_io_error(self):
        # A path whose parent is a FILE (not a dir) makes mkdir/append fail;
        # the recorder swallows it and returns {} rather than raising.
        bad_parent = Path(self.dir.name) / 'afile'
        bad_parent.write_text('x')
        entry = self._record(audit_log_path=bad_parent / 'sub' / 'log.jsonl')
        self.assertEqual(entry, {})

    def test_default_path_honors_env_override(self):
        with mock.patch.dict(
            os.environ, {'KATO_ACTION_GUARD_AUDIT_PATH': str(self.path)},
        ):
            self.assertEqual(audit.action_guard_audit_path(), self.path)


if __name__ == '__main__':
    unittest.main()
