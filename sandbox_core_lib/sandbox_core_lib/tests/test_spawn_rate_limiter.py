"""Spawn-rate limiter tests under simulated flood.

Closes Gap 5 from the test-coverage audit. The doc claims the
sandbox refuses to launch if more than 30 spawns landed in the audit
log within the last 60 seconds — caps runaway task-scan loops and
DoS-by-spawn-flood. Without these tests, a future change that bumps
the limit silently or removes the check entirely could ship.

Properties under test:

  * The rate-limit constant is the documented 30-per-60-seconds.
  * ``check_spawn_rate`` raises ``SandboxError`` once the count
    reaches or exceeds the limit.
  * ``record_spawn`` (the authoritative path used by spawns) refuses
    when the count is at the limit, regardless of whether
    ``check_spawn_rate`` was called separately first.
  * Entries older than the window do NOT count toward the limit.
  * The error message names the limit + window so the operator can
    diagnose without reading the source.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from sandbox_core_lib.sandbox_core_lib.manager import (
    _SPAWN_RATE_LIMIT,
    _SPAWN_RATE_WINDOW_SEC,
    SandboxError,
    check_spawn_rate,
    record_spawn,
)


def _write_synthetic_spawns(
    audit_path: Path,
    count: int,
    *,
    now: datetime,
    seconds_back: int = 0,
) -> None:
    """Append ``count`` synthetic spawn entries within the rate window.

    We don't go through ``record_spawn`` here so the test can shape the
    timestamps precisely (and so the test of record_spawn's refusal
    doesn't depend on it succeeding ``count`` times in a row first).
    """
    timestamp = (now - timedelta(seconds=seconds_back)).isoformat(timespec='seconds')
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with audit_path.open('ab') as fh:
        for i in range(count):
            entry = {
                'timestamp': timestamp,
                'event': 'spawn',
                'task_id': f'PROJ-SYNTH-{i}',
                'container_name': f'kato-sandbox-synth-{i:08d}',
                'image_tag': 'kato/claude-sandbox:test',
                'image_digest': 'sha256:' + 'd' * 64,
                'workspace_path': '/tmp/synth',
                'prev_hash': '0' * 64,
            }
            fh.write((json.dumps(entry) + '\n').encode('utf-8'))


class SpawnRateLimitConstantTests(unittest.TestCase):
    """Lock the rate-limit constants the doc names.

    A bump to the limit (e.g. 30 → 100) needs a matching doc update;
    this test fails fast if the doc-vs-code drift sneaks in.
    """

    def test_limit_is_30_spawns(self) -> None:
        self.assertEqual(_SPAWN_RATE_LIMIT, 30)

    def test_window_is_60_seconds(self) -> None:
        self.assertEqual(_SPAWN_RATE_WINDOW_SEC, 60)


class CheckSpawnRateTests(unittest.TestCase):
    """``check_spawn_rate`` — peek-mode rate check used by tooling/UI."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.audit_path = Path(self._tmp.name) / 'sandbox-audit.log'
        self.now = datetime.now(timezone.utc)

    def test_no_audit_log_returns_zero_silently(self) -> None:
        count = check_spawn_rate(self.audit_path, now=self.now)
        self.assertEqual(count, 0)

    def test_below_limit_returns_count_silently(self) -> None:
        _write_synthetic_spawns(self.audit_path, _SPAWN_RATE_LIMIT - 5, now=self.now)
        count = check_spawn_rate(self.audit_path, now=self.now)
        self.assertEqual(count, _SPAWN_RATE_LIMIT - 5)

    def test_one_below_limit_still_passes(self) -> None:
        _write_synthetic_spawns(self.audit_path, _SPAWN_RATE_LIMIT - 1, now=self.now)
        count = check_spawn_rate(self.audit_path, now=self.now)
        self.assertEqual(count, _SPAWN_RATE_LIMIT - 1)

    def test_at_limit_refuses(self) -> None:
        """The boundary itself triggers refusal — `>=`, not `>`."""
        _write_synthetic_spawns(self.audit_path, _SPAWN_RATE_LIMIT, now=self.now)
        with self.assertRaises(SandboxError) as cm:
            check_spawn_rate(self.audit_path, now=self.now)
        message = str(cm.exception)
        # Operator-facing message names both numbers so they can
        # diagnose without reading the source.
        self.assertIn(str(_SPAWN_RATE_LIMIT), message)
        self.assertIn(str(_SPAWN_RATE_WINDOW_SEC), message)
        self.assertIn('rate exceeded', message)

    def test_above_limit_refuses(self) -> None:
        _write_synthetic_spawns(self.audit_path, _SPAWN_RATE_LIMIT + 10, now=self.now)
        with self.assertRaises(SandboxError):
            check_spawn_rate(self.audit_path, now=self.now)

    def test_old_entries_outside_window_do_not_count(self) -> None:
        # Synthesize 100 entries from 2 minutes ago — well outside the
        # 60-second window. Not one of them should count.
        _write_synthetic_spawns(
            self.audit_path,
            100,
            now=self.now,
            seconds_back=120,
        )
        count = check_spawn_rate(self.audit_path, now=self.now)
        self.assertEqual(count, 0)

    def test_mixed_old_and_new_only_counts_new(self) -> None:
        # 100 ancient entries (don't count) + 5 recent (do count).
        _write_synthetic_spawns(
            self.audit_path, 100, now=self.now, seconds_back=120,
        )
        _write_synthetic_spawns(
            self.audit_path, 5, now=self.now, seconds_back=0,
        )
        count = check_spawn_rate(self.audit_path, now=self.now)
        self.assertEqual(count, 5)


class RecordSpawnEnforcesRateLimitTests(unittest.TestCase):
    """The authoritative path also enforces the limit (not just check_spawn_rate)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.audit_path = Path(self._tmp.name) / 'sandbox-audit.log'
        self.now = datetime.now(timezone.utc)

    def _record(self) -> None:
        with patch(
            'sandbox_core_lib.sandbox_core_lib.manager._image_digest',
            return_value='sha256:' + 'd' * 64,
        ):
            record_spawn(
                task_id='PROJ-OVERFLOW',
                container_name='kato-sandbox-overflow-eeee5555',
                workspace_path='/tmp/overflow',
                audit_log_path=self.audit_path,
            )

    def test_record_spawn_refuses_at_limit(self) -> None:
        """Even if check_spawn_rate isn't called, record_spawn enforces.

        This is the load-bearing invariant: a caller that forgets to
        peek with check_spawn_rate must still hit the wall, because
        record_spawn does the atomic check + write under the lock.
        """
        # Pre-fill the audit log to the limit with synthetic entries.
        _write_synthetic_spawns(self.audit_path, _SPAWN_RATE_LIMIT, now=self.now)

        with self.assertRaises(SandboxError) as cm:
            self._record()
        message = str(cm.exception)
        self.assertIn('rate exceeded', message)
        self.assertIn(str(_SPAWN_RATE_LIMIT), message)

    def test_record_spawn_succeeds_when_under_limit(self) -> None:
        """Smoke test the happy path so the test above isn't trivially passing."""
        _write_synthetic_spawns(
            self.audit_path, _SPAWN_RATE_LIMIT - 1, now=self.now,
        )
        # Should not raise.
        self._record()
        # And the new entry was actually appended.
        lines = [ln for ln in self.audit_path.read_bytes().splitlines() if ln.strip()]
        self.assertEqual(len(lines), _SPAWN_RATE_LIMIT)

    def test_record_spawn_succeeds_when_old_entries_have_expired(self) -> None:
        """Burst window is rolling — old bursts don't permanently lock the operator out."""
        # 100 ancient entries should not block a new spawn now.
        _write_synthetic_spawns(
            self.audit_path, 100, now=self.now, seconds_back=120,
        )
        # Should not raise.
        self._record()


if __name__ == '__main__':
    unittest.main()
