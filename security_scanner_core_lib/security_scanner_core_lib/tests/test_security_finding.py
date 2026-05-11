"""Full coverage for security_finding.py.

Tests every method and edge case on Severity, SecurityFinding, and ScanReport.
"""
from __future__ import annotations

import unittest

from security_scanner_core_lib.security_scanner_core_lib.security_finding import (
    ScanReport,
    SecurityFinding,
    Severity,
)


# ---------------------------------------------------------------------------
# Severity
# ---------------------------------------------------------------------------


class SeverityEnumTests(unittest.TestCase):
    def test_values_are_lowercase_strings(self) -> None:
        self.assertEqual(Severity.CRITICAL.value, 'critical')
        self.assertEqual(Severity.HIGH.value, 'high')
        self.assertEqual(Severity.MEDIUM.value, 'medium')
        self.assertEqual(Severity.LOW.value, 'low')

    def test_str_inherits_value(self) -> None:
        self.assertEqual(str(Severity.HIGH), 'high')

    def test_is_at_least_strict_upper(self) -> None:
        self.assertTrue(Severity.CRITICAL.is_at_least(Severity.CRITICAL))
        self.assertTrue(Severity.CRITICAL.is_at_least(Severity.HIGH))
        self.assertTrue(Severity.CRITICAL.is_at_least(Severity.MEDIUM))
        self.assertTrue(Severity.CRITICAL.is_at_least(Severity.LOW))

    def test_is_at_least_high(self) -> None:
        self.assertFalse(Severity.HIGH.is_at_least(Severity.CRITICAL))
        self.assertTrue(Severity.HIGH.is_at_least(Severity.HIGH))
        self.assertTrue(Severity.HIGH.is_at_least(Severity.MEDIUM))
        self.assertTrue(Severity.HIGH.is_at_least(Severity.LOW))

    def test_is_at_least_medium(self) -> None:
        self.assertFalse(Severity.MEDIUM.is_at_least(Severity.CRITICAL))
        self.assertFalse(Severity.MEDIUM.is_at_least(Severity.HIGH))
        self.assertTrue(Severity.MEDIUM.is_at_least(Severity.MEDIUM))
        self.assertTrue(Severity.MEDIUM.is_at_least(Severity.LOW))

    def test_is_at_least_low(self) -> None:
        self.assertFalse(Severity.LOW.is_at_least(Severity.CRITICAL))
        self.assertFalse(Severity.LOW.is_at_least(Severity.HIGH))
        self.assertFalse(Severity.LOW.is_at_least(Severity.MEDIUM))
        self.assertTrue(Severity.LOW.is_at_least(Severity.LOW))

    def test_from_string_canonical_values(self) -> None:
        self.assertEqual(Severity.from_string('critical'), Severity.CRITICAL)
        self.assertEqual(Severity.from_string('high'), Severity.HIGH)
        self.assertEqual(Severity.from_string('medium'), Severity.MEDIUM)
        self.assertEqual(Severity.from_string('low'), Severity.LOW)

    def test_from_string_uppercase(self) -> None:
        self.assertEqual(Severity.from_string('CRITICAL'), Severity.CRITICAL)
        self.assertEqual(Severity.from_string('HIGH'), Severity.HIGH)

    def test_from_string_mixed_case_with_whitespace(self) -> None:
        self.assertEqual(Severity.from_string('  Medium  '), Severity.MEDIUM)

    def test_from_string_empty_string_falls_back_to_low(self) -> None:
        self.assertEqual(Severity.from_string(''), Severity.LOW)

    def test_from_string_none_falls_back_to_low(self) -> None:
        self.assertEqual(Severity.from_string(None), Severity.LOW)  # type: ignore[arg-type]

    def test_from_string_unknown_value_falls_back_to_low(self) -> None:
        self.assertEqual(Severity.from_string('unknown'), Severity.LOW)
        self.assertEqual(Severity.from_string('xyz'), Severity.LOW)
        self.assertEqual(Severity.from_string('moderate'), Severity.LOW)


# ---------------------------------------------------------------------------
# SecurityFinding
# ---------------------------------------------------------------------------


class SecurityFindingTests(unittest.TestCase):
    def _make(self, **kwargs) -> SecurityFinding:
        defaults = dict(
            tool='test-tool',
            severity=Severity.HIGH,
            rule_id='T-001',
            message='test finding',
        )
        defaults.update(kwargs)
        return SecurityFinding(**defaults)

    def test_defaults_path_and_line_are_empty(self) -> None:
        f = self._make()
        self.assertEqual(f.path, '')
        self.assertEqual(f.line, 0)
        self.assertEqual(f.metadata, ())

    def test_dedup_key_includes_tool_rule_path_line(self) -> None:
        f = self._make(tool='bandit', rule_id='B601', path='app.py', line=10)
        self.assertEqual(f.dedup_key(), ('bandit', 'B601', 'app.py', 10))

    def test_dedup_key_with_empty_path_and_zero_line(self) -> None:
        f = self._make(tool='safety', rule_id='CVE-2023-1234')
        self.assertEqual(f.dedup_key(), ('safety', 'CVE-2023-1234', '', 0))

    def test_two_findings_with_same_key_are_equal(self) -> None:
        f1 = self._make(path='x.py', line=5)
        f2 = self._make(path='x.py', line=5)
        self.assertEqual(f1, f2)

    def test_two_findings_with_different_metadata_are_unequal(self) -> None:
        f1 = self._make(metadata=(('k', 'v1'),))
        f2 = self._make(metadata=(('k', 'v2'),))
        self.assertNotEqual(f1, f2)

    def test_findings_are_hashable(self) -> None:
        f = self._make()
        self.assertIn(f, {f})

    def test_to_dict_severity_is_string_value(self) -> None:
        f = self._make(severity=Severity.CRITICAL)
        d = f.to_dict()
        self.assertEqual(d['severity'], 'critical')

    def test_to_dict_metadata_becomes_dict(self) -> None:
        f = self._make(metadata=(('pkg', 'requests'), ('cvss', '8.1')))
        d = f.to_dict()
        self.assertEqual(d['metadata'], {'pkg': 'requests', 'cvss': '8.1'})

    def test_to_dict_empty_metadata_is_empty_dict(self) -> None:
        f = self._make()
        self.assertEqual(f.to_dict()['metadata'], {})

    def test_to_dict_contains_all_fields(self) -> None:
        f = self._make(tool='env-file', rule_id='env-real-credential',
                       path='.env', line=3)
        d = f.to_dict()
        self.assertIn('tool', d)
        self.assertIn('severity', d)
        self.assertIn('rule_id', d)
        self.assertIn('message', d)
        self.assertIn('path', d)
        self.assertIn('line', d)
        self.assertIn('metadata', d)


# ---------------------------------------------------------------------------
# ScanReport
# ---------------------------------------------------------------------------


class ScanReportTests(unittest.TestCase):
    def _make_finding(self, severity: Severity, *, tool: str = 't',
                      rule_id: str = 'R') -> SecurityFinding:
        return SecurityFinding(tool=tool, severity=severity,
                               rule_id=rule_id, message='m')

    def test_by_severity_returns_matching_findings(self) -> None:
        crit = self._make_finding(Severity.CRITICAL)
        high = self._make_finding(Severity.HIGH)
        med = self._make_finding(Severity.MEDIUM)
        report = ScanReport(findings=(crit, high, med), blocking=True,
                            block_threshold=Severity.HIGH)
        self.assertEqual(report.by_severity(Severity.CRITICAL), (crit,))
        self.assertEqual(report.by_severity(Severity.HIGH), (high,))
        self.assertEqual(report.by_severity(Severity.LOW), ())

    def test_has_findings_at_least_true_when_critical_and_threshold_is_high(self) -> None:
        crit = self._make_finding(Severity.CRITICAL)
        report = ScanReport(findings=(crit,), blocking=True,
                            block_threshold=Severity.HIGH)
        self.assertTrue(report.has_findings_at_least(Severity.HIGH))
        self.assertTrue(report.has_findings_at_least(Severity.CRITICAL))

    def test_has_findings_at_least_false_when_only_medium_findings(self) -> None:
        med = self._make_finding(Severity.MEDIUM)
        report = ScanReport(findings=(med,), blocking=False,
                            block_threshold=Severity.HIGH)
        self.assertFalse(report.has_findings_at_least(Severity.HIGH))
        self.assertTrue(report.has_findings_at_least(Severity.MEDIUM))

    def test_empty_findings_has_no_findings_at_any_level(self) -> None:
        report = ScanReport(findings=(), blocking=False,
                            block_threshold=Severity.HIGH)
        for severity in Severity:
            self.assertFalse(report.has_findings_at_least(severity))

    def test_to_dict_structure(self) -> None:
        f = self._make_finding(Severity.HIGH)
        report = ScanReport(
            findings=(f,),
            blocking=True,
            block_threshold=Severity.HIGH,
            runner_errors=(('safety', 'network error'),),
        )
        d = report.to_dict()
        self.assertIs(d['blocking'], True)
        self.assertEqual(d['block_threshold'], 'high')
        self.assertEqual(len(d['findings']), 1)
        self.assertEqual(d['runner_errors'],
                         [{'runner': 'safety', 'error': 'network error'}])

    def test_to_dict_empty_report(self) -> None:
        report = ScanReport(findings=(), blocking=False,
                            block_threshold=Severity.CRITICAL)
        d = report.to_dict()
        self.assertIs(d['blocking'], False)
        self.assertEqual(d['findings'], [])
        self.assertEqual(d['runner_errors'], [])

    def test_to_dict_multiple_runner_errors(self) -> None:
        report = ScanReport(
            findings=(), blocking=False, block_threshold=Severity.HIGH,
            runner_errors=(('bandit', 'err1'), ('npm-audit', 'err2')),
        )
        d = report.to_dict()
        self.assertEqual(len(d['runner_errors']), 2)
        self.assertEqual(d['runner_errors'][0]['runner'], 'bandit')
        self.assertEqual(d['runner_errors'][1]['runner'], 'npm-audit')

    def test_findings_tuple_is_frozen(self) -> None:
        report = ScanReport(findings=(), blocking=False,
                            block_threshold=Severity.HIGH)
        with self.assertRaises((TypeError, AttributeError)):
            report.findings = ()  # type: ignore[misc]


if __name__ == '__main__':
    unittest.main()
