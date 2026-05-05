"""Tests for the external audit-log shipping (OG2).

Closes the tail-truncation residual the audit-log section of
BYPASS_PROTECTIONS.md names. The local hash chain detects mid-chain
tampering; tail-truncation followed by fresh appends produces a
valid-looking chain. Shipping each entry to an external sink as
it's written gives operators a reference copy.

Properties under test:

  * ``ship_audit_entry`` is a no-op when no target is configured
    (so existing operators see no behaviour change).
  * ``https://`` target POSTs JSON, raises ``AuditShipError`` on
    network/HTTP failure.
  * ``file://`` target appends a JSON line; entries from concurrent
    spawns interleave atomically (we use ``O_APPEND``).
  * ``http://`` (plaintext) is refused — audit entries can carry
    sensitive paths/IDs.
  * Best-effort by default: a sink failure logs a WARNING and
    returns silently.
  * ``KATO_SANDBOX_AUDIT_SHIP_REQUIRED=true`` promotes failures to
    ``AuditShipError`` so the spawn is refused.
  * Cross-OS: stdlib only, no platform-specific code paths.
"""

from __future__ import annotations

import json
import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from sandbox_core_lib.sandbox_core_lib.audit_log_shipping import (
    AuditShipError,
    is_shipping_enabled,
    ship_audit_entry,
)


_FAKE_ENTRY = {
    'timestamp': '2026-05-03T12:00:00+00:00',
    'event': 'spawn',
    'task_id': 'PROJ-1',
    'container_name': 'kato-sandbox-PROJ-1-aaaa1111',
    'image_tag': 'kato/claude-sandbox:test',
    'image_digest': 'sha256:' + 'd' * 64,
    'workspace_path': '/tmp/workspace',
    'prev_hash': '0' * 64,
}


class IsShippingEnabledTests(unittest.TestCase):
    def test_no_env_var_means_disabled(self) -> None:
        self.assertFalse(is_shipping_enabled({}))

    def test_empty_env_var_means_disabled(self) -> None:
        self.assertFalse(
            is_shipping_enabled({'KATO_SANDBOX_AUDIT_SHIP_TARGET': ''})
        )

    def test_whitespace_env_var_means_disabled(self) -> None:
        # Whitespace-only is the same as empty — operator's stale
        # export shouldn't activate shipping accidentally.
        self.assertFalse(
            is_shipping_enabled({'KATO_SANDBOX_AUDIT_SHIP_TARGET': '   '})
        )

    def test_https_target_means_enabled(self) -> None:
        self.assertTrue(
            is_shipping_enabled({
                'KATO_SANDBOX_AUDIT_SHIP_TARGET': 'https://example.com/sink',
            }),
        )

    def test_file_target_means_enabled(self) -> None:
        self.assertTrue(
            is_shipping_enabled({
                'KATO_SANDBOX_AUDIT_SHIP_TARGET': 'file:///tmp/sink.log',
            }),
        )


class ShipAuditEntryNoOpTests(unittest.TestCase):
    """When no target is configured, the function returns silently.

    Existing operators (no ``KATO_SANDBOX_AUDIT_SHIP_TARGET`` set)
    must see zero behaviour change — no log lines, no exceptions,
    no network calls.
    """

    def test_no_target_does_not_raise(self) -> None:
        ship_audit_entry(_FAKE_ENTRY, env={})  # no exception

    def test_no_target_does_not_call_urlopen(self) -> None:
        with patch(
            'sandbox_core_lib.sandbox_core_lib.audit_log_shipping.urlopen',
        ) as mock_urlopen:
            ship_audit_entry(_FAKE_ENTRY, env={})
        mock_urlopen.assert_not_called()


class HttpsShippingTests(unittest.TestCase):
    def test_https_target_posts_json_body(self) -> None:
        mock_response = MagicMock()
        mock_response.getcode.return_value = 200
        mock_response.__enter__.return_value = mock_response
        mock_response.__exit__.return_value = False
        with patch(
            'sandbox_core_lib.sandbox_core_lib.audit_log_shipping.urlopen',
            return_value=mock_response,
        ) as mock_urlopen:
            ship_audit_entry(
                _FAKE_ENTRY,
                env={'KATO_SANDBOX_AUDIT_SHIP_TARGET': 'https://sink.example/audit'},
            )

        mock_urlopen.assert_called_once()
        request = mock_urlopen.call_args.args[0]
        self.assertEqual(request.full_url, 'https://sink.example/audit')
        self.assertEqual(request.get_method(), 'POST')
        body = json.loads(request.data.decode('utf-8'))
        self.assertEqual(body, _FAKE_ENTRY)
        # Content-Type tells the sink it's JSON.
        self.assertEqual(request.get_header('Content-type'), 'application/json')

    def test_http_plaintext_is_refused(self) -> None:
        # Plaintext shipping is refused because audit entries carry
        # workspace paths and task IDs the operator may consider
        # sensitive. Operators who genuinely need plaintext run a
        # forwarder.
        with patch(
            'sandbox_core_lib.sandbox_core_lib.audit_log_shipping.urlopen',
        ) as mock_urlopen, self.assertRaises(AuditShipError) as cm:
            ship_audit_entry(
                _FAKE_ENTRY,
                env={
                    'KATO_SANDBOX_AUDIT_SHIP_TARGET': 'http://sink.example/audit',
                    'KATO_SANDBOX_AUDIT_SHIP_REQUIRED': 'true',
                },
            )
        # The dispatcher falls through to the unsupported-scheme branch
        # for http:// — the operator-facing message in that branch
        # explicitly names the http:// rejection rationale.
        message = str(cm.exception)
        self.assertIn('http://', message)
        self.assertIn('refused', message)
        mock_urlopen.assert_not_called()

    def test_https_non_2xx_status_raises_when_required(self) -> None:
        mock_response = MagicMock()
        mock_response.getcode.return_value = 500
        mock_response.__enter__.return_value = mock_response
        mock_response.__exit__.return_value = False
        with patch(
            'sandbox_core_lib.sandbox_core_lib.audit_log_shipping.urlopen',
            return_value=mock_response,
        ), self.assertRaises(AuditShipError) as cm:
            ship_audit_entry(
                _FAKE_ENTRY,
                env={
                    'KATO_SANDBOX_AUDIT_SHIP_TARGET': 'https://sink.example/audit',
                    'KATO_SANDBOX_AUDIT_SHIP_REQUIRED': 'true',
                },
            )
        self.assertIn('status 500', str(cm.exception))

    def test_https_network_failure_raises_when_required(self) -> None:
        with patch(
            'sandbox_core_lib.sandbox_core_lib.audit_log_shipping.urlopen',
            side_effect=OSError('connection refused'),
        ), self.assertRaises(AuditShipError) as cm:
            ship_audit_entry(
                _FAKE_ENTRY,
                env={
                    'KATO_SANDBOX_AUDIT_SHIP_TARGET': 'https://sink.example/audit',
                    'KATO_SANDBOX_AUDIT_SHIP_REQUIRED': 'true',
                },
            )
        self.assertIn('connection refused', str(cm.exception))


class FileShippingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.sink_path = Path(self._tmp.name) / 'audit-sink.log'

    def test_file_target_appends_json_line(self) -> None:
        ship_audit_entry(
            _FAKE_ENTRY,
            env={'KATO_SANDBOX_AUDIT_SHIP_TARGET': f'file://{self.sink_path}'},
        )

        lines = self.sink_path.read_bytes().splitlines()
        self.assertEqual(len(lines), 1)
        roundtripped = json.loads(lines[0])
        self.assertEqual(roundtripped, _FAKE_ENTRY)

    def test_file_target_appends_each_call_atomically(self) -> None:
        # Three sequential ship calls — the sink should have three
        # complete JSON lines, no interleaving.
        for i in range(3):
            entry = dict(_FAKE_ENTRY, task_id=f'PROJ-{i}')
            ship_audit_entry(
                entry,
                env={'KATO_SANDBOX_AUDIT_SHIP_TARGET': f'file://{self.sink_path}'},
            )

        lines = self.sink_path.read_bytes().splitlines()
        self.assertEqual(len(lines), 3)
        for i, line in enumerate(lines):
            entry = json.loads(line)
            self.assertEqual(entry['task_id'], f'PROJ-{i}')

    def test_file_target_creates_parent_directories(self) -> None:
        nested_path = Path(self._tmp.name) / 'deep' / 'nested' / 'audit-sink.log'
        ship_audit_entry(
            _FAKE_ENTRY,
            env={'KATO_SANDBOX_AUDIT_SHIP_TARGET': f'file://{nested_path}'},
        )
        self.assertTrue(nested_path.exists())


class BestEffortByDefaultTests(unittest.TestCase):
    """Without ``KATO_SANDBOX_AUDIT_SHIP_REQUIRED``, ship failures are warnings."""

    def test_failure_without_required_flag_does_not_raise(self) -> None:
        with patch(
            'sandbox_core_lib.sandbox_core_lib.audit_log_shipping.urlopen',
            side_effect=OSError('connection refused'),
        ):
            # No exception — best-effort.
            ship_audit_entry(
                _FAKE_ENTRY,
                env={'KATO_SANDBOX_AUDIT_SHIP_TARGET': 'https://sink.example/audit'},
            )

    def test_failure_without_required_flag_logs_warning(self) -> None:
        logger = logging.getLogger('test_audit_log_shipping')
        with patch(
            'sandbox_core_lib.sandbox_core_lib.audit_log_shipping.urlopen',
            side_effect=OSError('connection refused'),
        ), self.assertLogs(logger=logger, level='WARNING') as cm:
            ship_audit_entry(
                _FAKE_ENTRY,
                env={'KATO_SANDBOX_AUDIT_SHIP_TARGET': 'https://sink.example/audit'},
                logger=logger,
            )
        joined = ' '.join(cm.output)
        # Warning names the override env so operators see how to
        # change the policy.
        self.assertIn('KATO_SANDBOX_AUDIT_SHIP_REQUIRED', joined)
        # And names the actual failure so they can diagnose.
        self.assertIn('connection refused', joined)


class UnsupportedSchemeTests(unittest.TestCase):
    def test_unknown_scheme_is_refused(self) -> None:
        # ftp://, s3://, syslog:// and friends are not supported in
        # this MVP. Adding them means adding a branch + a unit test.
        with self.assertRaises(AuditShipError) as cm:
            ship_audit_entry(
                _FAKE_ENTRY,
                env={
                    'KATO_SANDBOX_AUDIT_SHIP_TARGET': 'ftp://sink.example/audit',
                    'KATO_SANDBOX_AUDIT_SHIP_REQUIRED': 'true',
                },
            )
        self.assertIn('unsupported scheme', str(cm.exception))


class RecordSpawnIntegrationTests(unittest.TestCase):
    """Drift-guard: ``record_spawn`` actually invokes ``ship_audit_entry``.

    The unit tests above prove the shipping module behaves correctly
    in isolation. This class proves the production caller — the
    sandbox audit-log writer in ``manager.record_spawn`` — actually
    reaches that module after every successful local write. Without
    this, a future refactor could drop the call site entirely and
    every unit test would still pass while OG2's "Closed" status in
    BYPASS_PROTECTIONS.md silently became false.

    The test patches ``ship_audit_entry`` at its import site inside
    ``record_spawn`` (the function does a deferred import to keep
    the manager's module-load fast), runs a spawn, and asserts the
    patch was called with the same JSON-serialisable entry the local
    log received. We do not patch ``record_spawn``'s local write —
    the integration we are locking is "local-then-ship", not "ship
    in place of local".
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.audit_path = Path(self._tmp.name) / 'sandbox-audit.log'

    def _spawn(self, *, env: dict | None = None) -> None:
        from sandbox_core_lib.sandbox_core_lib.manager import record_spawn

        with patch(
            'sandbox_core_lib.sandbox_core_lib.manager._image_digest',
            return_value='sha256:' + 'd' * 64,
        ):
            record_spawn(
                task_id='PROJ-1',
                container_name='kato-sandbox-PROJ-1-aaaa1111',
                workspace_path='/tmp/workspace',
                audit_log_path=self.audit_path,
                env=env,
            )

    def test_record_spawn_calls_ship_audit_entry_after_local_write(self) -> None:
        # The deferred import inside ``record_spawn`` resolves
        # ``ship_audit_entry`` from ``sandbox_core_lib.sandbox_core_lib.audit_log_shipping``.
        # Patch there so the real module is what we're locking.
        with patch(
            'sandbox_core_lib.sandbox_core_lib.audit_log_shipping.ship_audit_entry',
        ) as mock_ship:
            self._spawn(env={'PATH': '/usr/bin'})

        # The local write must have happened (drift-guard against a
        # refactor that turns this into ship-only).
        self.assertTrue(self.audit_path.exists())
        local_lines = self.audit_path.read_text().splitlines()
        self.assertEqual(len(local_lines), 1)
        local_entry = json.loads(local_lines[0])

        # And the shipper was called once with the same entry the
        # local log received.
        mock_ship.assert_called_once()
        call_kwargs = mock_ship.call_args
        shipped_entry = call_kwargs.args[0] if call_kwargs.args else call_kwargs.kwargs['entry']
        self.assertEqual(shipped_entry['task_id'], local_entry['task_id'])
        self.assertEqual(shipped_entry['prev_hash'], local_entry['prev_hash'])
        self.assertEqual(shipped_entry['container_name'], local_entry['container_name'])

    def test_record_spawn_promotes_required_failure_to_sandbox_error(self) -> None:
        # When operators set ``KATO_SANDBOX_AUDIT_SHIP_REQUIRED=true``,
        # an ``AuditShipError`` from the shipper must surface as a
        # ``SandboxError`` so callers refuse the spawn. Without this,
        # the "fail-closed" promise in the doc is decorative.
        from sandbox_core_lib.sandbox_core_lib.manager import SandboxError

        with patch(
            'sandbox_core_lib.sandbox_core_lib.audit_log_shipping.ship_audit_entry',
            side_effect=AuditShipError('sink unreachable'),
        ):
            with self.assertRaises(SandboxError) as cm:
                self._spawn(env={'KATO_SANDBOX_AUDIT_SHIP_REQUIRED': 'true'})

        self.assertIn('audit-log shipping failed', str(cm.exception))
        # The local write still happened (we ship AFTER local commit
        # so we never lose an entry to a sink failure).
        self.assertTrue(self.audit_path.exists())


if __name__ == '__main__':
    unittest.main()
