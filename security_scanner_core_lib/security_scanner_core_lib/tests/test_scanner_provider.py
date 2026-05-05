"""Drift guard for the ScannerProvider Protocol shape."""

from __future__ import annotations

import unittest

from security_scanner_core_lib.security_scanner_core_lib.scanner_provider import (
    ScannerProvider,
)


class _CompliantRunnerModule(object):
    """Smallest object that satisfies ``ScannerProvider`` — proves
    the runtime-checkable Protocol accepts a duck-typed runner
    without inheritance, which is the on-ramp every existing
    runner module relies on."""

    def run(self, workspace_path, logger=None, timeout_seconds=None):
        return []


class _MissingRunMethodModule(object):
    """No ``run`` method — must NOT satisfy the Protocol."""

    def scan(self, *args, **kwargs):
        return []


class ScannerProviderProtocolTests(unittest.TestCase):
    def test_compliant_runner_satisfies_protocol(self) -> None:
        self.assertIsInstance(_CompliantRunnerModule(), ScannerProvider)

    def test_module_without_run_does_not_satisfy_protocol(self) -> None:
        self.assertNotIsInstance(_MissingRunMethodModule(), ScannerProvider)


if __name__ == '__main__':
    unittest.main()
