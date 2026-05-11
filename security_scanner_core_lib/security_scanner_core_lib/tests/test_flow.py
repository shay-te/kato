"""A-Z flow tests for the security_scanner_core_lib pipeline.

Each test walks the full stack from workspace on disk through runner
invocation, aggregation, deduplication, blocking decision, and
summariser output — end-to-end with real filesystem I/O where
possible, subprocess calls mocked where external tools are needed.

Flow coverage:
  F1  Clean workspace → non-blocking empty report
  F2  Real credential in .env → CRITICAL finding → blocking report
  F3  Multiple runners, findings aggregated and deduped
  F4  High-severity finding + custom threshold blocks
  F5  Runner unavailable → error recorded, scan continues
  F6  Disabled scanner → always clean non-blocking
  F7  Disabled runner in otherwise-enabled config → skipped
  F8  Timeout of slow runner → error recorded, fast runner survives
  F9  Blocking report → SecurityScanBlocked carries report + message
  F10 Summarizer output round-trip (ticket + email) for blocked report
  F11 Custom tool_name propagates through summarizer
  F12 Full pipeline with placeholder_annotation override silences env finding
  F13 env-file runner + bandit runner together (bandit mocked)
  F14 Report to_dict() is JSON-serialisable
"""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from security_scanner_core_lib.security_scanner_core_lib.runners import env_file_runner
from security_scanner_core_lib.security_scanner_core_lib.runners._helpers import (
    RunnerUnavailableError,
)
from security_scanner_core_lib.security_scanner_core_lib.security_finding import (
    ScanReport,
    SecurityFinding,
    Severity,
)
from security_scanner_core_lib.security_scanner_core_lib.security_scanner_service import (
    RunnerConfig,
    SecurityScanBlocked,
    SecurityScannerConfig,
    SecurityScannerService,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_finding(severity: Severity, *, tool: str = 't', rule_id: str = 'R',
                  path: str = 'f.py', line: int = 1) -> SecurityFinding:
    return SecurityFinding(tool=tool, severity=severity, rule_id=rule_id,
                           message='flow finding', path=path, line=line)


class _WorkspaceFixture:
    def __init__(self, test: unittest.TestCase) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        test.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def write(self, relpath: str, content: str) -> Path:
        p = self.root / relpath
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding='utf-8')
        return p

    def __str__(self) -> str:
        return str(self.root)


# ---------------------------------------------------------------------------
# F1  Clean workspace → non-blocking empty report
# ---------------------------------------------------------------------------


class F1CleanWorkspaceTest(unittest.TestCase):
    def test_clean_workspace_returns_non_blocking_empty_report(self) -> None:
        ws = _WorkspaceFixture(self)
        ws.write('app.py', 'x = 1\n')

        service = SecurityScannerService(SecurityScannerConfig(
            enabled=True,
            runners=[RunnerConfig('env-file', env_file_runner.run, timeout_seconds=30)],
        ))
        report = service.scan_workspace(str(ws))

        self.assertFalse(report.blocking)
        self.assertEqual(report.findings, ())
        self.assertEqual(report.runner_errors, ())


# ---------------------------------------------------------------------------
# F2  Real credential in .env → CRITICAL finding → blocking report
# ---------------------------------------------------------------------------


class F2RealCredentialBlocksTest(unittest.TestCase):
    def test_real_env_credential_produces_blocking_report(self) -> None:
        ws = _WorkspaceFixture(self)
        ws.write('.env', 'AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY\n')

        service = SecurityScannerService(SecurityScannerConfig(
            enabled=True,
            block_on_severity=(Severity.CRITICAL, Severity.HIGH),
            runners=[RunnerConfig('env-file', env_file_runner.run, timeout_seconds=30)],
        ))
        report = service.scan_workspace(str(ws))

        self.assertTrue(report.blocking)
        self.assertEqual(len(report.findings), 1)
        self.assertEqual(report.findings[0].severity, Severity.CRITICAL)
        self.assertEqual(report.findings[0].tool, 'env-file')
        self.assertIn('AWS_SECRET_ACCESS_KEY', report.findings[0].message)


# ---------------------------------------------------------------------------
# F3  Multiple runners aggregate and deduplicate findings
# ---------------------------------------------------------------------------


class F3MultiRunnerAggregationTest(unittest.TestCase):
    def test_findings_from_multiple_runners_are_combined_and_deduped(self) -> None:
        shared = _make_finding(Severity.HIGH, tool='shared', rule_id='DUP', path='x.py', line=5)
        unique = _make_finding(Severity.MEDIUM, tool='runner_b', rule_id='UNIQUE')

        def runner_a(_path, **_kw): return [shared]
        def runner_b(_path, **_kw): return [shared, unique]  # shared is a dup

        service = SecurityScannerService(SecurityScannerConfig(
            enabled=True,
            runners=[
                RunnerConfig('runner_a', runner_a),
                RunnerConfig('runner_b', runner_b),
            ],
        ))
        report = service.scan_workspace('/')

        self.assertEqual(len(report.findings), 2)
        rule_ids = {f.rule_id for f in report.findings}
        self.assertIn('DUP', rule_ids)
        self.assertIn('UNIQUE', rule_ids)


# ---------------------------------------------------------------------------
# F4  High-severity finding + custom threshold blocks
# ---------------------------------------------------------------------------


class F4CustomThresholdTest(unittest.TestCase):
    def test_medium_finding_blocks_when_threshold_is_medium(self) -> None:
        def runner(_path, **_kw): return [_make_finding(Severity.MEDIUM)]

        service = SecurityScannerService(SecurityScannerConfig(
            enabled=True,
            block_on_severity=(Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM),
            runners=[RunnerConfig('r', runner)],
        ))
        report = service.scan_workspace('/')
        self.assertTrue(report.blocking)
        self.assertEqual(report.block_threshold, Severity.MEDIUM)

    def test_medium_finding_does_not_block_when_threshold_is_high(self) -> None:
        def runner(_path, **_kw): return [_make_finding(Severity.MEDIUM)]

        service = SecurityScannerService(SecurityScannerConfig(
            enabled=True,
            block_on_severity=(Severity.CRITICAL, Severity.HIGH),
            runners=[RunnerConfig('r', runner)],
        ))
        report = service.scan_workspace('/')
        self.assertFalse(report.blocking)


# ---------------------------------------------------------------------------
# F5  Runner unavailable → error recorded, scan continues
# ---------------------------------------------------------------------------


class F5RunnerUnavailableTest(unittest.TestCase):
    def test_unavailable_runner_records_error_and_continues(self) -> None:
        def broken(_path, **_kw): raise RunnerUnavailableError('tool missing')
        def working(_path, **_kw): return [_make_finding(Severity.HIGH, tool='ok')]

        service = SecurityScannerService(SecurityScannerConfig(
            enabled=True,
            runners=[
                RunnerConfig('broken', broken),
                RunnerConfig('ok', working),
            ],
        ))
        report = service.scan_workspace('/')

        self.assertEqual(len(report.findings), 1)
        self.assertEqual(report.findings[0].tool, 'ok')
        self.assertEqual(len(report.runner_errors), 1)
        self.assertEqual(report.runner_errors[0][0], 'broken')
        self.assertIn('tool missing', report.runner_errors[0][1])

    def test_crashed_runner_records_error_and_continues(self) -> None:
        def crashed(_path, **_kw): raise RuntimeError('unexpected crash')
        def ok(_path, **_kw): return []

        service = SecurityScannerService(SecurityScannerConfig(
            enabled=True,
            runners=[RunnerConfig('crashed', crashed), RunnerConfig('ok', ok)],
        ))
        report = service.scan_workspace('/')
        self.assertEqual(len(report.runner_errors), 1)
        self.assertIn('unexpected crash', report.runner_errors[0][1])


# ---------------------------------------------------------------------------
# F6  Disabled scanner → always clean non-blocking
# ---------------------------------------------------------------------------


class F6DisabledScannerTest(unittest.TestCase):
    def test_disabled_scanner_always_returns_clean(self) -> None:
        ws = _WorkspaceFixture(self)
        ws.write('.env', 'AWS_KEY=AKIAREALLEAKEDKEYEXAMPLE12345\n')

        service = SecurityScannerService(SecurityScannerConfig(
            enabled=False,
            runners=[RunnerConfig('env-file', env_file_runner.run)],
        ))
        report = service.scan_workspace(str(ws))

        self.assertFalse(report.blocking)
        self.assertEqual(report.findings, ())
        self.assertFalse(service.enabled)


# ---------------------------------------------------------------------------
# F7  Disabled runner in otherwise-enabled config → skipped
# ---------------------------------------------------------------------------


class F7DisabledRunnerTest(unittest.TestCase):
    def test_disabled_runner_is_not_invoked(self) -> None:
        invoked = []

        def should_not_run(_path, **_kw):
            invoked.append(True)
            return [_make_finding(Severity.CRITICAL)]

        service = SecurityScannerService(SecurityScannerConfig(
            enabled=True,
            runners=[RunnerConfig('r', should_not_run, enabled=False)],
        ))
        report = service.scan_workspace('/')

        self.assertEqual(invoked, [])
        self.assertEqual(report.findings, ())
        self.assertFalse(report.blocking)


# ---------------------------------------------------------------------------
# F8  Timeout of slow runner → error recorded, fast runner survives
# ---------------------------------------------------------------------------


class F8TimeoutTest(unittest.TestCase):
    def test_slow_runner_times_out_fast_runner_result_preserved(self) -> None:
        def slow(_path, **_kw):
            time.sleep(3)
            return [_make_finding(Severity.CRITICAL, tool='slow')]

        def fast(_path, **_kw):
            return [_make_finding(Severity.HIGH, tool='fast')]

        service = SecurityScannerService(SecurityScannerConfig(
            enabled=True,
            runners=[
                RunnerConfig('slow', slow, timeout_seconds=1),
                RunnerConfig('fast', fast, timeout_seconds=10),
            ],
        ))
        report = service.scan_workspace('/')

        tools = [f.tool for f in report.findings]
        self.assertIn('fast', tools)
        self.assertNotIn('slow', tools)
        self.assertEqual(len(report.runner_errors), 1)
        self.assertIn('timeout', report.runner_errors[0][1].lower())


# ---------------------------------------------------------------------------
# F9  Blocking report → SecurityScanBlocked carries report + message
# ---------------------------------------------------------------------------


class F9SecurityScanBlockedTest(unittest.TestCase):
    def test_blocked_exception_carries_report(self) -> None:
        report = ScanReport(
            findings=(_make_finding(Severity.CRITICAL),),
            blocking=True,
            block_threshold=Severity.HIGH,
        )
        exc = SecurityScanBlocked(report)

        self.assertIs(exc.report, report)
        self.assertIsInstance(exc, RuntimeError)
        msg = str(exc)
        self.assertIn('1 critical', msg)

    def test_blocked_can_be_raised_from_scan_result(self) -> None:
        def critical(_path, **_kw): return [_make_finding(Severity.CRITICAL)]

        service = SecurityScannerService(SecurityScannerConfig(
            enabled=True,
            block_on_severity=(Severity.CRITICAL, Severity.HIGH),
            runners=[RunnerConfig('r', critical)],
        ))
        report = service.scan_workspace('/')
        self.assertTrue(report.blocking)

        with self.assertRaises(SecurityScanBlocked):
            if report.blocking:
                raise SecurityScanBlocked(report)


# ---------------------------------------------------------------------------
# F10 Summarizer output round-trip (ticket + email) for blocked report
# ---------------------------------------------------------------------------


class F10SummarizerRoundTripTest(unittest.TestCase):
    def test_ticket_and_email_round_trip_for_blocked_report(self) -> None:
        report = ScanReport(
            findings=(
                SecurityFinding(
                    tool='env-file', severity=Severity.CRITICAL,
                    rule_id='env-real-credential',
                    message='AWS_KEY in .env looks like a real credential',
                    path='.env', line=2,
                ),
                SecurityFinding(
                    tool='bandit', severity=Severity.HIGH,
                    rule_id='B602', message='Subprocess with shell=True',
                    path='scripts/deploy.py', line=42,
                ),
            ),
            blocking=True,
            block_threshold=Severity.HIGH,
            runner_errors=(('safety', 'network unreachable'),),
        )
        service = SecurityScannerService()
        ticket_body = service.summarize_for_ticket(report)
        subject, email_body = service.summarize_for_email(report)

        # ticket
        self.assertIn('refused', ticket_body.lower())
        self.assertIn('env-file', ticket_body)
        self.assertIn('bandit', ticket_body)
        self.assertIn('safety', ticket_body)
        self.assertIn('network unreachable', ticket_body)
        # email
        self.assertIn('BLOCKED', subject)
        self.assertIn('2 security finding', subject)
        self.assertEqual(ticket_body, email_body)


# ---------------------------------------------------------------------------
# F11 Custom tool_name propagates through summarizer
# ---------------------------------------------------------------------------


class F11CustomToolNameTest(unittest.TestCase):
    def test_custom_tool_name_in_ticket_and_email(self) -> None:
        service = SecurityScannerService(SecurityScannerConfig(
            tool_name='acme-scanner',
            runners=[],
        ))
        report = ScanReport(findings=(), blocking=False, block_threshold=Severity.HIGH)

        ticket = service.summarize_for_ticket(report)
        subject, _ = service.summarize_for_email(report)

        self.assertIn('acme-scanner', ticket)
        self.assertIn('acme-scanner', subject)


# ---------------------------------------------------------------------------
# F12 placeholder_annotation override silences env finding
# ---------------------------------------------------------------------------


class F12PlaceholderAnnotationOverrideTest(unittest.TestCase):
    def test_custom_annotation_silences_line(self) -> None:
        ws = _WorkspaceFixture(self)
        ws.write('.env', '\n'.join([
            'AWS_KEY=AKIAREALLYLOOKSREAL12345  # acme:placeholder',
            'OTHER_KEY=ANOTHERREALVALUE123456  # not silenced',
        ]))

        def annotated_env_runner(workspace_path, *, logger=None, timeout_seconds=None):
            return env_file_runner.run(
                workspace_path, logger,
                placeholder_annotation='acme:placeholder',
            )

        service = SecurityScannerService(SecurityScannerConfig(
            enabled=True,
            runners=[RunnerConfig('env-file', annotated_env_runner)],
        ))
        report = service.scan_workspace(str(ws))

        self.assertEqual(len(report.findings), 1)
        self.assertIn('OTHER_KEY', report.findings[0].message)


# ---------------------------------------------------------------------------
# F13 env-file runner + mocked bandit runner together
# ---------------------------------------------------------------------------


class F13CombinedRunnersTest(unittest.TestCase):
    def test_env_and_bandit_findings_combined(self) -> None:
        ws = _WorkspaceFixture(self)
        ws.write('.env', 'STRIPE_KEY=synthetic_wHaTaScArYsTrInGoFcHaRs1234567890\n')
        ws.write('app.py', 'import subprocess\nsubprocess.call(cmd, shell=True)\n')

        bandit_payload = json.dumps({
            'results': [{
                'test_id': 'B602',
                'test_name': 'subprocess_popen_with_shell_equals_true',
                'issue_severity': 'HIGH',
                'issue_confidence': 'HIGH',
                'issue_text': 'subprocess call with shell=True.',
                'filename': str(ws.root / 'app.py'),
                'line_number': 2,
            }]
        })

        bandit_result = MagicMock()
        bandit_result.returncode = 1
        bandit_result.stdout = bandit_payload
        bandit_result.stderr = ''

        with patch(
            'security_scanner_core_lib.security_scanner_core_lib.runners.bandit_runner.shutil.which',
            return_value='/usr/bin/bandit',
        ), patch(
            'security_scanner_core_lib.security_scanner_core_lib.runners.bandit_runner.subprocess.run',
            return_value=bandit_result,
        ):
            from security_scanner_core_lib.security_scanner_core_lib.runners import bandit_runner
            service = SecurityScannerService(SecurityScannerConfig(
                enabled=True,
                block_on_severity=(Severity.CRITICAL, Severity.HIGH),
                runners=[
                    RunnerConfig('env-file', env_file_runner.run, timeout_seconds=30),
                    RunnerConfig('bandit', bandit_runner.run, timeout_seconds=30),
                ],
            ))
            report = service.scan_workspace(str(ws))

        tools = {f.tool for f in report.findings}
        self.assertIn('env-file', tools)
        self.assertIn('bandit', tools)
        self.assertTrue(report.blocking)


# ---------------------------------------------------------------------------
# F14 Report to_dict() is JSON-serialisable
# ---------------------------------------------------------------------------


class F14ReportJsonSerialisableTest(unittest.TestCase):
    def test_full_report_is_json_serialisable(self) -> None:
        report = ScanReport(
            findings=(
                SecurityFinding(
                    tool='env-file', severity=Severity.CRITICAL,
                    rule_id='env-real-credential',
                    message='Key looks real',
                    path='.env', line=1,
                    metadata=(('key', 'AWS_KEY'),),
                ),
            ),
            blocking=True,
            block_threshold=Severity.HIGH,
            runner_errors=(('safety', 'network error'),),
        )
        d = report.to_dict()
        serialised = json.dumps(d)  # must not raise
        restored = json.loads(serialised)

        self.assertTrue(restored['blocking'])
        self.assertEqual(restored['block_threshold'], 'high')
        self.assertEqual(len(restored['findings']), 1)
        self.assertEqual(restored['findings'][0]['severity'], 'critical')
        self.assertEqual(restored['findings'][0]['metadata']['key'], 'AWS_KEY')
        self.assertEqual(restored['runner_errors'][0]['runner'], 'safety')


if __name__ == '__main__':
    unittest.main()
