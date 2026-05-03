"""Tamper-evidence tests for the sandbox audit log's hash chain.

Closes Gap 4 from the test-coverage audit: the doc claims "editing
any past entry invalidates every subsequent chain link and is
detectable", but the *implementation* of that property wasn't locked
by a test that actually performs the tamper and asserts the
detection.

The property under test (from BYPASS_PROTECTIONS.md "Operational
hardening" → "What the hash chain proves (and doesn't)"):

  * Each appended JSON line carries a ``prev_hash`` field equal to
    ``sha256(previous_line_raw_bytes)``.
  * If a past entry is modified (even one byte), every subsequent
    ``prev_hash`` no longer matches the recomputed sha256 of the
    line it claims to chain from. Detectable offline with
    ``sha256sum`` per line — no secret needed.

What the chain does NOT prove (out of scope here, named in the doc):

  * Completeness — a tail-truncation followed by fresh appends
    produces a valid chain rooted at the new tail. Closing that
    requires external append-only storage; tracked as named open
    gap OG2 in the doc.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kato_core_lib.sandbox.manager import (
    _AUDIT_GENESIS_HASH,
    _last_audit_chain_hash,
    record_spawn,
)


def _read_lines(path: Path) -> list[bytes]:
    """Return the audit log's raw lines (with no trailing newline byte).

    Reads as bytes — the chain hashes the raw bytes of each line, so
    a test that decodes to str and re-encodes could mask a bug.
    """
    return [ln for ln in path.read_bytes().splitlines() if ln.strip()]


def _expected_prev_hash(line_bytes: bytes) -> str:
    """sha256(line_bytes) — the chain function applied to one line."""
    return hashlib.sha256(line_bytes).hexdigest()


class AuditChainIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.audit_path = Path(self._tmp.name) / 'sandbox-audit.log'

    def _spawn(self, *, task_id: str, container_name: str) -> None:
        """Wrapper that mocks the image-digest lookup so no Docker is needed."""
        with patch(
            'kato_core_lib.sandbox.manager._image_digest',
            return_value='sha256:' + 'd' * 64,
        ):
            record_spawn(
                task_id=task_id,
                container_name=container_name,
                workspace_path='/tmp/workspace',
                audit_log_path=self.audit_path,
            )

    # ---- happy path: chain links match line-by-line ----

    def test_first_entry_prev_hash_is_genesis(self) -> None:
        """Genesis: first entry chains to ``'0' * 64``."""
        self._spawn(task_id='PROJ-1', container_name='kato-sandbox-PROJ-1-aaaa1111')

        lines = _read_lines(self.audit_path)
        self.assertEqual(len(lines), 1)
        entry = json.loads(lines[0])
        self.assertEqual(entry['prev_hash'], _AUDIT_GENESIS_HASH)

    def test_subsequent_entries_chain_to_prior_line_bytes(self) -> None:
        """Each entry's prev_hash == sha256(previous-line-raw-bytes)."""
        self._spawn(task_id='PROJ-1', container_name='kato-sandbox-PROJ-1-aaaa1111')
        self._spawn(task_id='PROJ-2', container_name='kato-sandbox-PROJ-2-bbbb2222')
        self._spawn(task_id='PROJ-3', container_name='kato-sandbox-PROJ-3-cccc3333')

        lines = _read_lines(self.audit_path)
        self.assertEqual(len(lines), 3)
        # Walk every chain link.
        entries = [json.loads(line) for line in lines]
        self.assertEqual(entries[0]['prev_hash'], _AUDIT_GENESIS_HASH)
        self.assertEqual(entries[1]['prev_hash'], _expected_prev_hash(lines[0]))
        self.assertEqual(entries[2]['prev_hash'], _expected_prev_hash(lines[1]))

    def test_last_chain_hash_helper_returns_sha256_of_tail(self) -> None:
        """``_last_audit_chain_hash`` returns the chain head for the next write."""
        self._spawn(task_id='PROJ-A', container_name='kato-sandbox-PROJ-A-deadbe01')
        self._spawn(task_id='PROJ-B', container_name='kato-sandbox-PROJ-B-deadbe02')

        lines = _read_lines(self.audit_path)
        head = _last_audit_chain_hash(self.audit_path)
        self.assertEqual(head, _expected_prev_hash(lines[-1]))

    # ---- tamper-evidence ----

    def test_mid_chain_tamper_breaks_subsequent_link(self) -> None:
        """Editing any past entry invalidates every subsequent chain link.

        This is the core tamper-evidence property. Without this test,
        a regression that changed how lines are framed (e.g. trailing
        whitespace normalization) could silently make tampering
        undetectable.
        """
        # Three entries: tampering with the middle one should leave
        # entry 0 valid, entry 1 invalid (we changed it), and entry 2's
        # chain link to it broken.
        self._spawn(task_id='PROJ-1', container_name='kato-sandbox-PROJ-1-aaaa1111')
        self._spawn(task_id='PROJ-2', container_name='kato-sandbox-PROJ-2-bbbb2222')
        self._spawn(task_id='PROJ-3', container_name='kato-sandbox-PROJ-3-cccc3333')

        original_lines = _read_lines(self.audit_path)

        # Pre-tamper: every chain link valid.
        original_entries = [json.loads(line) for line in original_lines]
        self.assertEqual(
            original_entries[2]['prev_hash'],
            _expected_prev_hash(original_lines[1]),
        )

        # Tamper: rewrite the middle entry's container_name. Even a
        # single-character difference changes the line's bytes and
        # therefore changes its sha256.
        tampered_entry = dict(original_entries[1])
        tampered_entry['container_name'] = 'kato-sandbox-EVIL-bbbb2222'
        tampered_line = json.dumps(tampered_entry, ensure_ascii=False).encode('utf-8')
        new_lines = [
            original_lines[0],
            tampered_line,
            original_lines[2],
        ]
        self.audit_path.write_bytes(b'\n'.join(new_lines) + b'\n')

        # Post-tamper: entry 2's prev_hash no longer matches sha256
        # of the (now-tampered) line 1 bytes. Detectable by recomputing.
        post_lines = _read_lines(self.audit_path)
        post_entries = [json.loads(line) for line in post_lines]
        self.assertNotEqual(
            post_entries[2]['prev_hash'],
            _expected_prev_hash(post_lines[1]),
            'tampering must break the chain link to the tampered entry',
        )
        # Entry 0's link to genesis is still valid (it was untouched).
        self.assertEqual(post_entries[0]['prev_hash'], _AUDIT_GENESIS_HASH)

    def test_tampering_with_first_entry_breaks_second_link(self) -> None:
        """Genesis-adjacent tamper still detectable — no special case."""
        self._spawn(task_id='PROJ-1', container_name='kato-sandbox-PROJ-1-aaaa1111')
        self._spawn(task_id='PROJ-2', container_name='kato-sandbox-PROJ-2-bbbb2222')

        original_lines = _read_lines(self.audit_path)
        original_entries = [json.loads(line) for line in original_lines]

        # Tamper with entry 0.
        tampered_entry = dict(original_entries[0])
        tampered_entry['workspace_path'] = '/tmp/EVIL'
        tampered_line = json.dumps(tampered_entry, ensure_ascii=False).encode('utf-8')
        self.audit_path.write_bytes(
            tampered_line + b'\n' + original_lines[1] + b'\n'
        )

        # Entry 1's chain link to entry 0 is now broken.
        post_lines = _read_lines(self.audit_path)
        post_entry_1 = json.loads(post_lines[1])
        self.assertNotEqual(
            post_entry_1['prev_hash'],
            _expected_prev_hash(post_lines[0]),
        )

    def test_appending_extends_chain_against_post_tamper_tail(self) -> None:
        """Tail-truncation residual (named in doc) — appending after a
        truncation produces a valid-looking chain rooted at the new tail.

        This test EXPECTS the chain to "look valid" against the new tail
        — that's the documented limitation. If the chain function
        gained completeness somehow, this test would be the regression
        signal. Tracked as named open gap OG2 (external audit-log
        shipping with append-only storage).
        """
        self._spawn(task_id='PROJ-1', container_name='kato-sandbox-PROJ-1-aaaa1111')
        self._spawn(task_id='PROJ-2', container_name='kato-sandbox-PROJ-2-bbbb2222')
        self._spawn(task_id='PROJ-3', container_name='kato-sandbox-PROJ-3-cccc3333')

        # Truncate to the first entry only — simulate a tail-cut.
        first_line = _read_lines(self.audit_path)[0]
        self.audit_path.write_bytes(first_line + b'\n')

        # Append a fresh entry. The new entry's prev_hash chains
        # to the (truncated) tail — a verifier that walks links sees
        # a valid chain and CANNOT detect the truncation. This is
        # the documented residual; the test locks the property as
        # "documented limitation, not implementation bug."
        self._spawn(task_id='PROJ-4', container_name='kato-sandbox-PROJ-4-dddd4444')

        post_lines = _read_lines(self.audit_path)
        self.assertEqual(len(post_lines), 2)
        post_entry_1 = json.loads(post_lines[1])
        self.assertEqual(
            post_entry_1['prev_hash'],
            _expected_prev_hash(post_lines[0]),
            'post-truncation appends still chain validly to the new tail '
            '(documented residual — closed by OG2 external append-only sink)',
        )


if __name__ == '__main__':
    unittest.main()
