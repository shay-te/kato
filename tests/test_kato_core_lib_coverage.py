"""Coverage for ``KatoCoreLib`` static / builder methods."""

from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from kato_core_lib.kato_core_lib import KatoCoreLib, _EmailCoreLibProxy


class EmailCoreLibProxyTests(unittest.TestCase):
    """Lines 91-93: ``_EmailCoreLibProxy.__call__`` lazy-imports the
    real ``EmailCoreLib`` constructor when invoked."""

    def test_proxy_delegates_to_real_email_core_lib(self) -> None:
        proxy = _EmailCoreLibProxy()
        fake_module = MagicMock()
        fake_module.EmailCoreLib.return_value = 'real-email-lib-instance'
        with patch.dict(
            'sys.modules',
            {'email_core_lib.email_core_lib': fake_module},
            clear=False,
        ):
            result = proxy('cfg')
        fake_module.EmailCoreLib.assert_called_once_with('cfg')
        self.assertEqual(result, 'real-email-lib-instance')


class BuildSecurityScannerServiceTests(unittest.TestCase):
    """Lines 368-420: the scanner-config translation path. Builds a
    SecurityScannerService from ``scanner_cfg`` honoring enabled,
    block_on_severity, per-runner toggles, and timeout overrides."""

    def test_uses_default_when_scanner_cfg_missing(self) -> None:
        # Lines 362-367: ``if scanner_cfg is None`` → default.
        # MagicMock auto-creates attributes, so we use SimpleNamespace
        # to force the missing-attr path.
        open_cfg = SimpleNamespace()
        instance = KatoCoreLib.__new__(KatoCoreLib)
        result = instance._build_security_scanner_service(open_cfg)
        self.assertIsNotNone(result)

    def test_uses_default_severities_when_block_on_severity_absent(self) -> None:
        # ``block_on_severity is None`` → critical-only default.
        # Matches the YAML default: HIGH+ findings surface as warnings
        # but don't refuse the task (transitive-dep CVE noise on
        # routine codebases shouldn't be a hard gate).
        from security_scanner_core_lib.security_scanner_core_lib.security_finding import (
            Severity,
        )
        scanner_cfg = SimpleNamespace(enabled=True, block_on_severity=None,
                                       runners=None, timeouts=None)
        open_cfg = SimpleNamespace(security_scanner=scanner_cfg)
        instance = KatoCoreLib.__new__(KatoCoreLib)
        service = instance._build_security_scanner_service(open_cfg)
        self.assertEqual(
            service._config.block_on_severity, (Severity.CRITICAL,),
        )

    def test_honors_explicit_block_on_severity_and_runner_toggles(self) -> None:
        # Lines 369-420: full traversal of the scanner-cfg branch.
        from security_scanner_core_lib.security_scanner_core_lib.security_finding import (
            Severity,
        )
        scanner_cfg = SimpleNamespace(
            enabled=True,
            block_on_severity=['critical', 'high'],
            runners=SimpleNamespace(
                env_file=True,
                detect_secrets=False,  # disabled
                bandit=True,
                safety=False,
                npm_audit=False,
            ),
            timeouts=SimpleNamespace(
                secrets=10,
                dependencies=30,
                code_patterns=20,
            ),
        )
        open_cfg = SimpleNamespace(security_scanner=scanner_cfg)
        instance = KatoCoreLib.__new__(KatoCoreLib)
        service = instance._build_security_scanner_service(open_cfg)
        # Runner list was rebuilt to drop disabled ones.
        runner_names = [r.name for r in service._config.runners]
        self.assertNotIn('detect-secrets', runner_names)
        self.assertNotIn('safety', runner_names)
        self.assertIn('bandit', runner_names)
        # Block severities include CRITICAL + HIGH.
        self.assertIn(Severity.CRITICAL, service._config.block_on_severity)

    def test_skips_none_timeout_values_and_safety_fallback(self) -> None:
        """Covers branch 432->426 (None timeout skipped) and 435->439
        (no ``safety`` override → no ``npm-audit`` setdefault)."""
        # ``dependencies`` is None → the per-key ``if value is not None``
        # check skips it (432->426). ``secrets`` is also None so
        # ``safety`` never lands in timeout_overrides, exercising the
        # False branch of ``if 'safety' in timeout_overrides`` (435->439).
        scanner_cfg = SimpleNamespace(
            enabled=True,
            block_on_severity=None,
            runners=None,
            timeouts=SimpleNamespace(
                secrets=None,
                dependencies=None,
                code_patterns=15,
            ),
        )
        open_cfg = SimpleNamespace(security_scanner=scanner_cfg)
        instance = KatoCoreLib.__new__(KatoCoreLib)
        service = instance._build_security_scanner_service(open_cfg)
        # bandit (code_patterns) override took effect; safety/npm-audit
        # kept their defaults because nothing seeded them.
        timeouts_by_name = {
            r.name: r.timeout_seconds for r in service._config.runners
        }
        self.assertEqual(timeouts_by_name.get('bandit'), 15)


class BuildRuntimePostureSupplierTests(unittest.TestCase):
    """Lines 446-452: the supplier closure inspects the live scanner
    config to decide ``scanner_blocks_at_medium``."""

    def test_supplier_reads_scanner_blocks_at_medium(self) -> None:
        from security_scanner_core_lib.security_scanner_core_lib.security_finding import (
            Severity,
        )
        scanner = MagicMock()
        scanner._config.block_on_severity = (Severity.MEDIUM, Severity.HIGH)
        supplier = KatoCoreLib._build_runtime_posture_supplier(
            security_scanner_service=scanner,
            bypass_permissions=True,
            docker_mode_on=False,
        )
        posture = supplier()
        self.assertTrue(posture.bypass_permissions)
        self.assertFalse(posture.docker_mode_on)
        self.assertTrue(posture.scanner_blocks_at_medium)

    def test_supplier_handles_missing_scanner(self) -> None:
        supplier = KatoCoreLib._build_runtime_posture_supplier(
            security_scanner_service=None,
            bypass_permissions=False,
            docker_mode_on=True,
        )
        posture = supplier()
        self.assertFalse(posture.scanner_blocks_at_medium)
        self.assertTrue(posture.docker_mode_on)

    def test_supplier_handles_scanner_without_config(self) -> None:
        """Covers branch 485->488: scanner present but ``_config`` is None."""
        scanner = SimpleNamespace(_config=None)
        supplier = KatoCoreLib._build_runtime_posture_supplier(
            security_scanner_service=scanner,
            bypass_permissions=False,
            docker_mode_on=False,
        )
        posture = supplier()
        self.assertFalse(posture.scanner_blocks_at_medium)


class ResolveTicketPlatformConfigTests(unittest.TestCase):
    """Line 484: raises when no per-platform config block is present."""

    def test_raises_when_platform_config_missing(self) -> None:
        # ``youtrack`` is the default platform — but no ``youtrack``
        # block on the open_cfg.
        open_cfg = SimpleNamespace(issue_platform='youtrack')
        with self.assertRaisesRegex(ValueError, 'missing issue platform config'):
            KatoCoreLib._resolve_ticket_platform_config(open_cfg)


class BringLessonsDocumentUpToDateTests(unittest.TestCase):
    """What boot does to the lessons document before any agent reads it."""

    @staticmethod
    def _service():
        service = MagicMock()
        service.filed = threading.Event()
        service.file_pending.side_effect = lambda: service.filed.set()
        service.has_pending.return_value = False
        return service

    def test_adopts_the_legacy_document_inline_then_files_in_the_background(self) -> None:
        service = self._service()
        with patch.dict(os.environ, {'KATO_ARCHITECTURE_DOC_PATH': '/docs/arch.md'}):
            KatoCoreLib._bring_lessons_document_up_to_date(service)
        # Adoption is a file copy and has already happened when boot moves on
        # — the gate is pointed at the document straight after.
        service.adopt_legacy_document.assert_called_once_with('/docs/arch.md')
        # Filing is an AI run per batch; the boot does not wait for it.
        self.assertTrue(service.filed.wait(timeout=2.0))

    def test_with_no_legacy_document_configured_it_still_files(self) -> None:
        service = self._service()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('KATO_ARCHITECTURE_DOC_PATH', None)
            KatoCoreLib._bring_lessons_document_up_to_date(service)
        service.adopt_legacy_document.assert_called_once_with('')
        self.assertTrue(service.filed.wait(timeout=2.0))

    def test_a_failed_adoption_neither_stops_the_boot_nor_the_filing(self) -> None:
        service = self._service()
        service.adopt_legacy_document.side_effect = RuntimeError('disk')
        KatoCoreLib._bring_lessons_document_up_to_date(service)  # no raise
        service.logger.exception.assert_called_once()
        self.assertTrue(service.filed.wait(timeout=2.0))

    def test_a_filing_crash_stays_inside_the_worker(self) -> None:
        service = MagicMock()
        service.has_pending.return_value = False
        crashed = threading.Event()

        def boom():
            crashed.set()
            raise RuntimeError('editor crashed')

        service.file_pending.side_effect = boom
        KatoCoreLib._bring_lessons_document_up_to_date(service)
        self.assertTrue(crashed.wait(timeout=2.0))


class KeepFilingTests(unittest.TestCase):
    """The backlog keeps being filed after a failed batch, until it is empty.

    One failed batch used to end the filing: the rest of the backlog sat until
    the next restart or promoted lesson. On the operator's machine six of ten
    batches waited a day and a half after a run failed in the night.
    """

    def _run(self, outcomes, pending_after):
        service = MagicMock()
        service.can_file = True
        service.file_pending.side_effect = list(outcomes)
        service.has_pending.side_effect = list(pending_after)
        waits: list[int] = []
        KatoCoreLib._keep_filing(service, sleep=waits.append)
        return service, waits

    def test_a_failed_batch_is_retried_until_the_backlog_is_empty(self) -> None:
        service, waits = self._run(
            outcomes=[True, False, True],
            pending_after=[True, True, False],
        )
        self.assertEqual(service.file_pending.call_count, 3)
        self.assertEqual(len(waits), 2)

    def test_the_wait_doubles_while_nothing_moves_and_is_capped(self) -> None:
        failures = 8
        _, waits = self._run(
            outcomes=[False] * failures + [True],
            pending_after=[True] * failures + [False],
        )
        self.assertEqual(waits, [300, 600, 1200, 2400, 3600, 3600, 3600, 3600])

    def test_progress_resets_the_wait(self) -> None:
        _, waits = self._run(
            outcomes=[False, False, True, False, True],
            pending_after=[True, True, True, True, False],
        )
        self.assertEqual(waits, [300, 600, 300, 300])

    def test_a_crash_counts_as_no_progress_not_as_the_end(self) -> None:
        service, waits = self._run(
            outcomes=[RuntimeError('editor crashed'), True],
            pending_after=[True, False],
        )
        self.assertEqual(service.file_pending.call_count, 2)
        self.assertEqual(waits, [300])

    def test_nothing_pending_means_one_call_and_no_wait(self) -> None:
        service, waits = self._run(outcomes=[False], pending_after=[False])
        self.assertEqual(service.file_pending.call_count, 1)
        self.assertEqual(waits, [])

    def test_without_an_editor_it_does_not_loop_at_all(self) -> None:
        service = MagicMock()
        service.can_file = False
        KatoCoreLib._keep_filing(service, sleep=self.fail)
        service.file_pending.assert_not_called()


class PointLessonsGateAtTests(unittest.TestCase):
    """The gate is armed exactly when there is something to read."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / 'lessons.md'
        patcher = patch.dict(os.environ, {'AGENT_LESSONS_PATH': 'stale'})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_document_with_content_arms_the_gate(self) -> None:
        self.path.write_text('- a rule\n', encoding='utf-8')
        KatoCoreLib._point_lessons_gate_at(self.path)
        self.assertEqual(os.environ['AGENT_LESSONS_PATH'], str(self.path))

    def test_a_blank_or_missing_document_disarms_it(self) -> None:
        # A gate with no directive would deny every tool while nothing said why.
        KatoCoreLib._point_lessons_gate_at(self.path)
        self.assertNotIn('AGENT_LESSONS_PATH', os.environ)

        os.environ['AGENT_LESSONS_PATH'] = 'stale'
        self.path.write_text('   \n', encoding='utf-8')
        KatoCoreLib._point_lessons_gate_at(self.path)
        self.assertNotIn('AGENT_LESSONS_PATH', os.environ)

    def test_an_unreadable_document_disarms_it(self) -> None:
        self.path.write_text('- a rule\n', encoding='utf-8')
        with patch.object(Path, 'read_text', side_effect=OSError('locked')):
            KatoCoreLib._point_lessons_gate_at(self.path)
        self.assertNotIn('AGENT_LESSONS_PATH', os.environ)


class ValidateRuntimeSourceFingerprintTests(unittest.TestCase):
    """Line 563: raises when the fingerprint doesn't match."""

    def test_returns_silently_when_fingerprint_absent(self) -> None:
        # Defensive: no expected fingerprint configured → no check.
        instance = KatoCoreLib.__new__(KatoCoreLib)
        # Use a dict-like config so .get() works.
        instance._validate_runtime_source_fingerprint(
            SimpleNamespace(get=lambda k, d=None: ''),
        )

    def test_raises_when_fingerprint_mismatches(self) -> None:
        instance = KatoCoreLib.__new__(KatoCoreLib)
        with patch(
            'kato_core_lib.kato_core_lib.runtime_source_fingerprint',
            return_value='actual-fingerprint',
        ):
            with self.assertRaisesRegex(RuntimeError, 'fingerprint mismatch'):
                instance._validate_runtime_source_fingerprint(
                    SimpleNamespace(get=lambda k, d=None:
                                    'different-fingerprint'
                                    if k == 'source_fingerprint' else d),
                )

    def test_returns_silently_when_fingerprint_matches(self) -> None:
        instance = KatoCoreLib.__new__(KatoCoreLib)
        with patch(
            'kato_core_lib.kato_core_lib.runtime_source_fingerprint',
            return_value='match',
        ):
            # No raise.
            instance._validate_runtime_source_fingerprint(
                SimpleNamespace(get=lambda k, d=None:
                                'match' if k == 'source_fingerprint' else d),
            )


if __name__ == '__main__':
    unittest.main()
