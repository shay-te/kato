"""End-to-end coverage for the security scanner pipeline.

Tests are organised by layer:

* ``EnvFileRunnerTests`` — exercises the placeholder-vs-real heuristic
  on real ``.env`` files written into a temp workspace.
* ``DataTypeTests`` — severity ladder + dataclass behaviour.
* ``SecurityScannerServiceTests`` — orchestrator: dedupe, threshold,
  timeout handling, runner-error surfacing, summariser output.
* ``SummariserTests`` — markdown ticket comment + email subject/body.
* ``ConfigTests`` — SecurityScannerConfig, block_threshold, default_config.
* ``SecurityScanBlockedTests`` — exception message and report attribute.
"""

from __future__ import annotations

import logging
import tempfile
import time
import unittest
from pathlib import Path

from security_scanner_core_lib.security_scanner_core_lib.security_finding import (
    ScanReport,
    SecurityFinding,
    Severity,
)
from security_scanner_core_lib.security_scanner_core_lib.runners import (
    env_file_runner,
)
from security_scanner_core_lib.security_scanner_core_lib.runners._helpers import (
    RunnerUnavailableError,
)
from security_scanner_core_lib.security_scanner_core_lib.security_scanner_service import (
    RunnerConfig,
    SecurityScanBlocked,
    SecurityScannerConfig,
    SecurityScannerService,
    default_config,
)


# ----- data types ------------------------------------------------------------


class DataTypeTests(unittest.TestCase):
    def test_severity_ladder_orders_correctly(self) -> None:
        self.assertTrue(Severity.CRITICAL.is_at_least(Severity.HIGH))
        self.assertTrue(Severity.HIGH.is_at_least(Severity.MEDIUM))
        self.assertTrue(Severity.HIGH.is_at_least(Severity.HIGH))
        self.assertFalse(Severity.MEDIUM.is_at_least(Severity.HIGH))
        self.assertFalse(Severity.LOW.is_at_least(Severity.MEDIUM))

    def test_severity_from_string_is_case_insensitive(self) -> None:
        self.assertEqual(Severity.from_string('CRITICAL'), Severity.CRITICAL)
        self.assertEqual(Severity.from_string('high'), Severity.HIGH)
        self.assertEqual(Severity.from_string('  Medium  '), Severity.MEDIUM)

    def test_severity_from_string_unknown_falls_back_to_low(self) -> None:
        self.assertEqual(Severity.from_string(''), Severity.LOW)
        self.assertEqual(Severity.from_string('made-up'), Severity.LOW)
        self.assertEqual(Severity.from_string(None), Severity.LOW)  # type: ignore[arg-type]

    def test_finding_dedup_key_uses_four_tuple(self) -> None:
        f = SecurityFinding(
            tool='env-file', severity=Severity.CRITICAL,
            rule_id='env-real-credential',
            message='leaked', path='backend/.env', line=3,
        )
        self.assertEqual(
            f.dedup_key(),
            ('env-file', 'env-real-credential', 'backend/.env', 3),
        )

    def test_scan_report_severity_filtering(self) -> None:
        crit = _make_finding(Severity.CRITICAL)
        med = _make_finding(Severity.MEDIUM)
        report = ScanReport(
            findings=(crit, med),
            blocking=True,
            block_threshold=Severity.HIGH,
        )
        self.assertEqual(report.by_severity(Severity.CRITICAL), (crit,))
        self.assertEqual(report.by_severity(Severity.MEDIUM), (med,))
        self.assertEqual(report.by_severity(Severity.LOW), ())
        self.assertTrue(report.has_findings_at_least(Severity.HIGH))
        self.assertFalse(report.has_findings_at_least(Severity.CRITICAL)
                         is False)  # CRITICAL >= CRITICAL

    def test_scan_report_to_dict_round_trips(self) -> None:
        report = ScanReport(
            findings=(_make_finding(Severity.HIGH),),
            blocking=True,
            block_threshold=Severity.HIGH,
            runner_errors=(('safety', 'network unreachable'),),
        )
        out = report.to_dict()
        self.assertEqual(out['blocking'], True)
        self.assertEqual(out['block_threshold'], 'high')
        self.assertEqual(len(out['findings']), 1)
        self.assertEqual(out['findings'][0]['severity'], 'high')
        self.assertEqual(
            out['runner_errors'],
            [{'runner': 'safety', 'error': 'network unreachable'}],
        )


# ----- env file runner -------------------------------------------------------


class EnvFileRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = Path(self._tmp.name)

    def _write(self, relpath: str, content: str) -> Path:
        path = self.workspace / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
        return path

    def test_empty_workspace_yields_no_findings(self) -> None:
        self.assertEqual(env_file_runner.run(str(self.workspace)), [])

    def test_env_example_is_exempt(self) -> None:
        self._write('.env.example', 'AWS_SECRET=AKIAIOSFODNN7EXAMPLE\n')
        self._write('.env.sample', 'API_KEY=sk-live-abcdefghijklmnopqrstuvwxyz\n')
        self._write('.env.template', 'DB_URL=postgres://user:pass@host/db\n')
        self.assertEqual(env_file_runner.run(str(self.workspace)), [])

    def test_real_env_with_real_looking_value_flags_critical(self) -> None:
        self._write('.env', 'AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY\n')
        findings = env_file_runner.run(str(self.workspace))
        self.assertEqual(len(findings), 1)
        f = findings[0]
        self.assertEqual(f.severity, Severity.CRITICAL)
        self.assertEqual(f.tool, 'env-file')
        self.assertEqual(f.path, '.env')
        self.assertEqual(f.line, 1)
        self.assertIn('AWS_SECRET_ACCESS_KEY', f.message)

    def test_placeholder_patterns_are_silenced(self) -> None:
        self._write('.env', '\n'.join([
            'API_KEY=',                          # blank
            'TOKEN=<your-api-key>',              # angle-bracket placeholder
            'PORT=5432',                         # bare number
            'DEBUG=true',                        # bool
            'HOST=localhost',                    # localhost
            'DB_HOST=127.0.0.1',                 # local IP
            'PASSWORD=replace-me',               # replace-me
            'SECRET=changeme',                   # changeme
            'KEY=YOUR_API_KEY_HERE',             # YOUR_*
            'OTHER=${SOME_ENV}',                 # interpolation reference
            'STAGE=development',                 # too short
            'PATH_TO=/var/log/app',              # filesystem path
            'LEVEL=INFO',                        # log level (short)
        ]))
        findings = env_file_runner.run(str(self.workspace))
        self.assertEqual(findings, [], f'expected no findings, got {findings}')

    def test_default_placeholder_annotation_silences(self) -> None:
        self._write('.env', '\n'.join([
            'AWS_KEY=AKIAREALLYLOOKSREAL12345  # security-scanner:placeholder',
            'AWS_KEY2=AKIATHISONELEAKSEXAMPLE  # not annotated',
        ]))
        findings = env_file_runner.run(str(self.workspace))
        self.assertEqual(len(findings), 1)
        self.assertIn('AWS_KEY2', findings[0].message)

    def test_custom_placeholder_annotation_silences(self) -> None:
        self._write('.env', '\n'.join([
            'AWS_KEY=AKIAREALLYLOOKSREAL12345  # myapp:placeholder',
            'AWS_KEY2=AKIATHISONELEAKSEXAMPLE  # not annotated',
        ]))
        findings = env_file_runner.run(
            str(self.workspace), placeholder_annotation='myapp:placeholder',
        )
        self.assertEqual(len(findings), 1)
        self.assertIn('AWS_KEY2', findings[0].message)

    def test_export_prefix_is_handled(self) -> None:
        self._write('.env', 'export AWS_KEY=AKIAEXAMPLEAKEYREALISH123\n')
        findings = env_file_runner.run(str(self.workspace))
        self.assertEqual(len(findings), 1)
        self.assertIn('AWS_KEY', findings[0].message)

    def test_quoted_values_are_unwrapped_before_check(self) -> None:
        self._write('.env', 'TOKEN="<your-key>"\n')
        self.assertEqual(env_file_runner.run(str(self.workspace)), [])

    def test_real_env_in_subdirectory_is_flagged(self) -> None:
        self._write(
            'backend/config/.env',
            'STRIPE_SECRET=synthetic_wHaTaScArYsTrInGoFcHaRs1234567890\n',
        )
        findings = env_file_runner.run(str(self.workspace))
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].path, 'backend/config/.env')

    def test_node_modules_subtree_is_ignored(self) -> None:
        self._write(
            'node_modules/whatever/.env',
            'API_KEY=AKIAfakebutlongenough123456789\n',
        )
        self.assertEqual(env_file_runner.run(str(self.workspace)), [])


# ----- orchestrator ----------------------------------------------------------


class SecurityScannerServiceTests(unittest.TestCase):
    def test_disabled_scanner_returns_empty_non_blocking_report(self) -> None:
        service = SecurityScannerService(SecurityScannerConfig(enabled=False))
        report = service.scan_workspace('/nonexistent')
        self.assertFalse(report.blocking)
        self.assertEqual(report.findings, ())
        self.assertEqual(report.runner_errors, ())

    def test_enabled_property_reflects_config(self) -> None:
        service_on = SecurityScannerService(SecurityScannerConfig(enabled=True))
        service_off = SecurityScannerService(SecurityScannerConfig(enabled=False))
        self.assertTrue(service_on.enabled)
        self.assertFalse(service_off.enabled)

    def test_no_active_runners_returns_clean_report(self) -> None:
        service = SecurityScannerService(SecurityScannerConfig(
            enabled=True, runners=[],
        ))
        report = service.scan_workspace('/nonexistent')
        self.assertFalse(report.blocking)

    def test_disabled_runner_is_skipped(self) -> None:
        called = []

        def runner_fn(_path, **_kw):
            called.append(True)
            return [_make_finding(Severity.CRITICAL)]

        service = SecurityScannerService(SecurityScannerConfig(
            enabled=True,
            runners=[RunnerConfig('r', runner_fn, enabled=False)],
        ))
        report = service.scan_workspace('/')
        self.assertEqual(called, [])
        self.assertEqual(report.findings, ())

    def test_runner_findings_are_aggregated(self) -> None:
        def runner_a(_path, **_kw): return [_make_finding(Severity.HIGH, tool='a')]
        def runner_b(_path, **_kw): return [_make_finding(Severity.MEDIUM, tool='b')]
        service = SecurityScannerService(SecurityScannerConfig(
            enabled=True,
            runners=[
                RunnerConfig('a', runner_a),
                RunnerConfig('b', runner_b),
            ],
        ))
        report = service.scan_workspace('/whatever')
        self.assertEqual(len(report.findings), 2)
        tools = {f.tool for f in report.findings}
        self.assertEqual(tools, {'a', 'b'})

    def test_blocking_decision_uses_threshold(self) -> None:
        def critical(_path, **_kw): return [_make_finding(Severity.CRITICAL)]
        def medium(_path, **_kw): return [_make_finding(Severity.MEDIUM, tool='med')]
        service = SecurityScannerService(SecurityScannerConfig(
            enabled=True,
            block_on_severity=(Severity.CRITICAL, Severity.HIGH),
            runners=[RunnerConfig('crit', critical)],
        ))
        self.assertTrue(service.scan_workspace('/').blocking)

        service2 = SecurityScannerService(SecurityScannerConfig(
            enabled=True,
            block_on_severity=(Severity.CRITICAL, Severity.HIGH),
            runners=[RunnerConfig('med', medium)],
        ))
        self.assertFalse(service2.scan_workspace('/').blocking)

    def test_strict_threshold_blocks_on_medium(self) -> None:
        def medium(_path, **_kw): return [_make_finding(Severity.MEDIUM)]
        service = SecurityScannerService(SecurityScannerConfig(
            enabled=True,
            block_on_severity=(
                Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM,
            ),
            runners=[RunnerConfig('med', medium)],
        ))
        self.assertTrue(service.scan_workspace('/').blocking)

    def test_runner_unavailable_becomes_runner_error_not_finding(self) -> None:
        def missing_tool(_path, **_kw):
            raise RunnerUnavailableError('safety not installed')
        service = SecurityScannerService(SecurityScannerConfig(
            enabled=True,
            runners=[RunnerConfig('safety', missing_tool)],
        ))
        report = service.scan_workspace('/')
        self.assertEqual(report.findings, ())
        self.assertFalse(report.blocking)
        self.assertEqual(len(report.runner_errors), 1)
        self.assertIn('safety not installed', report.runner_errors[0][1])

    def test_runner_crash_becomes_runner_error(self) -> None:
        def explodes(_path, **_kw):
            raise RuntimeError('oops')
        service = SecurityScannerService(SecurityScannerConfig(
            enabled=True,
            runners=[RunnerConfig('boom', explodes)],
        ))
        report = service.scan_workspace('/')
        self.assertFalse(report.blocking)
        self.assertEqual(len(report.runner_errors), 1)
        self.assertIn('oops', report.runner_errors[0][1])

    def test_runner_timeout_does_not_block_other_runners(self) -> None:
        def slow(_path, **_kw):
            time.sleep(2)
            return []
        def fast(_path, **_kw):
            return [_make_finding(Severity.MEDIUM, tool='fast')]
        service = SecurityScannerService(SecurityScannerConfig(
            enabled=True,
            runners=[
                RunnerConfig('slow', slow, timeout_seconds=1),
                RunnerConfig('fast', fast, timeout_seconds=10),
            ],
        ))
        report = service.scan_workspace('/')
        self.assertEqual([f.tool for f in report.findings], ['fast'])
        self.assertEqual(len(report.runner_errors), 1)
        self.assertEqual(report.runner_errors[0][0], 'slow')
        self.assertIn('timeout', report.runner_errors[0][1].lower())

    def test_findings_are_deduped(self) -> None:
        f = _make_finding(Severity.HIGH, tool='shared', rule_id='X', path='a.py', line=5)
        def r1(_path, **_kw): return [f]
        def r2(_path, **_kw): return [f]
        service = SecurityScannerService(SecurityScannerConfig(
            enabled=True,
            runners=[RunnerConfig('r1', r1), RunnerConfig('r2', r2)],
        ))
        self.assertEqual(len(service.scan_workspace('/').findings), 1)

    def test_runner_kwargs_are_passed_when_supported(self) -> None:
        captured = {}
        def runner(workspace_path, *, logger=None, timeout_seconds=None):
            captured['timeout'] = timeout_seconds
            captured['has_logger'] = logger is not None
            return []
        service = SecurityScannerService(SecurityScannerConfig(
            enabled=True,
            runners=[RunnerConfig('r', runner, timeout_seconds=42)],
        ))
        service.scan_workspace('/')
        self.assertEqual(captured['timeout'], 42)
        self.assertTrue(captured['has_logger'])

    def test_runner_without_timeout_kwarg_still_called(self) -> None:
        def runner(_path, *, logger=None):  # no timeout_seconds
            return []
        service = SecurityScannerService(SecurityScannerConfig(
            enabled=True,
            runners=[RunnerConfig('r', runner)],
        ))
        service.scan_workspace('/')


# ----- SecurityScanBlocked ---------------------------------------------------


class SecurityScanBlockedTests(unittest.TestCase):
    def test_is_runtime_error_subclass(self) -> None:
        self.assertTrue(issubclass(SecurityScanBlocked, RuntimeError))

    def test_message_includes_critical_and_high_counts(self) -> None:
        report = ScanReport(
            findings=(
                _make_finding(Severity.CRITICAL),
                _make_finding(Severity.CRITICAL),
                _make_finding(Severity.HIGH),
            ),
            blocking=True,
            block_threshold=Severity.HIGH,
        )
        exc = SecurityScanBlocked(report)
        msg = str(exc)
        self.assertIn('2 critical', msg)
        self.assertIn('1 high', msg)

    def test_report_attribute_is_attached(self) -> None:
        report = ScanReport(findings=(), blocking=True, block_threshold=Severity.HIGH)
        exc = SecurityScanBlocked(report)
        self.assertIs(exc.report, report)

    def test_can_be_raised_and_caught(self) -> None:
        report = ScanReport(findings=(), blocking=True, block_threshold=Severity.HIGH)
        with self.assertRaises(SecurityScanBlocked) as ctx:
            raise SecurityScanBlocked(report)
        self.assertIs(ctx.exception.report, report)

    def test_zero_critical_zero_high_message(self) -> None:
        report = ScanReport(findings=(), blocking=True, block_threshold=Severity.HIGH)
        exc = SecurityScanBlocked(report)
        self.assertIn('0 critical', str(exc))
        self.assertIn('0 high', str(exc))


# ----- Config ----------------------------------------------------------------


class SecurityScannerConfigTests(unittest.TestCase):
    def test_default_config_is_enabled(self) -> None:
        config = SecurityScannerConfig()
        self.assertTrue(config.enabled)

    def test_default_tool_name(self) -> None:
        config = SecurityScannerConfig()
        self.assertEqual(config.tool_name, 'security-scanner')

    def test_custom_tool_name(self) -> None:
        config = SecurityScannerConfig(tool_name='my-scanner')
        self.assertEqual(config.tool_name, 'my-scanner')

    def test_block_threshold_default_is_high(self) -> None:
        config = SecurityScannerConfig(
            block_on_severity=(Severity.CRITICAL, Severity.HIGH),
        )
        self.assertEqual(config.block_threshold(), Severity.HIGH)

    def test_block_threshold_strict_is_medium(self) -> None:
        config = SecurityScannerConfig(
            block_on_severity=(Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM),
        )
        self.assertEqual(config.block_threshold(), Severity.MEDIUM)

    def test_block_threshold_critical_only(self) -> None:
        config = SecurityScannerConfig(
            block_on_severity=(Severity.CRITICAL,),
        )
        self.assertEqual(config.block_threshold(), Severity.CRITICAL)

    def test_block_threshold_empty_defaults_to_critical(self) -> None:
        config = SecurityScannerConfig(block_on_severity=())
        self.assertEqual(config.block_threshold(), Severity.CRITICAL)

    def test_default_config_function_returns_all_runners(self) -> None:
        config = default_config()
        names = {r.name for r in config.runners}
        self.assertIn('env-file', names)
        self.assertIn('bandit', names)
        self.assertIn('safety', names)
        self.assertIn('npm-audit', names)
        self.assertIn('detect-secrets', names)

    def test_default_config_function_is_enabled(self) -> None:
        config = default_config()
        self.assertTrue(config.enabled)


# ----- summarisers -----------------------------------------------------------


class SummariserTests(unittest.TestCase):
    def _service(self, tool_name: str = 'security-scanner') -> SecurityScannerService:
        return SecurityScannerService(SecurityScannerConfig(tool_name=tool_name))

    def test_clean_report_summary_is_friendly(self) -> None:
        report = ScanReport(
            findings=(), blocking=False, block_threshold=Severity.HIGH,
        )
        body = self._service().summarize_for_ticket(report)
        self.assertIn('no findings', body)

    def test_blocking_report_starts_with_red_flag_and_lists_findings(self) -> None:
        report = ScanReport(
            findings=(
                _make_finding(Severity.CRITICAL, tool='env-file',
                              rule_id='env-real-credential',
                              message='AWS_KEY in .env looks real',
                              path='.env', line=3),
            ),
            blocking=True,
            block_threshold=Severity.HIGH,
        )
        body = self._service().summarize_for_ticket(report)
        self.assertIn('refused', body.lower())
        self.assertIn('CRITICAL'.lower(), body.lower())
        self.assertIn('env-file', body)
        self.assertIn('AWS_KEY', body)

    def test_warning_report_says_proceed(self) -> None:
        report = ScanReport(
            findings=(_make_finding(Severity.MEDIUM),),
            blocking=False,
            block_threshold=Severity.HIGH,
        )
        body = self._service().summarize_for_ticket(report)
        self.assertIn('proceed', body.lower())

    def test_email_subject_summarises_count_and_threshold(self) -> None:
        report = ScanReport(
            findings=(
                _make_finding(Severity.CRITICAL),
                _make_finding(Severity.HIGH),
            ),
            blocking=True,
            block_threshold=Severity.HIGH,
        )
        subject, body = self._service().summarize_for_email(report)
        self.assertIn('BLOCKED', subject)
        self.assertIn('2 security finding', subject)
        self.assertIn('high', subject)
        self.assertIn('refused', body.lower())

    def test_email_subject_warn_when_non_blocking_findings(self) -> None:
        report = ScanReport(
            findings=(_make_finding(Severity.MEDIUM),),
            blocking=False,
            block_threshold=Severity.HIGH,
        )
        subject, body = self._service().summarize_for_email(report)
        self.assertIn('WARN', subject)
        self.assertIn('1 security finding', subject)

    def test_email_subject_clean_when_no_findings(self) -> None:
        report = ScanReport(findings=(), blocking=False, block_threshold=Severity.HIGH)
        subject, _ = self._service().summarize_for_email(report)
        self.assertIn('clean', subject.lower())

    def test_runner_errors_appear_in_summary(self) -> None:
        report = ScanReport(
            findings=(),
            blocking=False,
            block_threshold=Severity.HIGH,
            runner_errors=(
                ('safety', 'network unreachable'),
                ('npm-audit', 'npm not installed'),
            ),
        )
        body = self._service().summarize_for_ticket(report)
        self.assertIn('Scanner warnings', body)
        self.assertIn('safety', body)
        self.assertIn('network unreachable', body)
        self.assertIn('npm-audit', body)

    def test_custom_tool_name_appears_in_ticket(self) -> None:
        report = ScanReport(findings=(), blocking=False, block_threshold=Severity.HIGH)
        body = self._service(tool_name='myorg-scanner').summarize_for_ticket(report)
        self.assertIn('myorg-scanner', body)

    def test_custom_tool_name_appears_in_email_subject(self) -> None:
        report = ScanReport(findings=(), blocking=False, block_threshold=Severity.HIGH)
        subject, _ = self._service(tool_name='myorg-scanner').summarize_for_email(report)
        self.assertIn('myorg-scanner', subject)

    def test_blocking_ticket_uses_tool_name(self) -> None:
        report = ScanReport(
            findings=(_make_finding(Severity.CRITICAL),),
            blocking=True,
            block_threshold=Severity.HIGH,
        )
        body = self._service(tool_name='custom-sec').summarize_for_ticket(report)
        self.assertIn('custom-sec', body)


# ----- helpers ---------------------------------------------------------------


def _make_finding(
    severity: Severity,
    *,
    tool: str = 'test',
    rule_id: str = 'TEST-001',
    message: str = 'test message',
    path: str = 'a.py',
    line: int = 1,
) -> SecurityFinding:
    return SecurityFinding(
        tool=tool, severity=severity, rule_id=rule_id,
        message=message, path=path, line=line,
    )


if __name__ == '__main__':
    unittest.main()
