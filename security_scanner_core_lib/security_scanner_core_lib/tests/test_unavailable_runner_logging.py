"""A missing optional scanner is said once, not once per scan."""

from __future__ import annotations

import logging
import unittest
from unittest import mock


class ScannerUnavailableIsSaidOnceTests(unittest.TestCase):
    """A missing optional scanner is a standing fact, not an event.

    Logged per scan it produced a dozen identical lines every few minutes
    and buried everything worth reading.
    """

    def _service_with_unavailable_runners(self, logger, names):
        from security_scanner_core_lib.security_scanner_core_lib.runners._helpers import (
            RunnerUnavailableError,
        )
        from security_scanner_core_lib.security_scanner_core_lib.security_scanner_service import (  # noqa: E501
            RunnerConfig,
            SecurityScannerConfig,
            SecurityScannerService,
        )

        def _missing(name):
            def _run(*_args, **_kwargs):
                raise RunnerUnavailableError(f'{name} is not installed')
            return _run

        return SecurityScannerService(
            config=SecurityScannerConfig(runners=[
                RunnerConfig(name=name, fn=_missing(name), enabled=True)
                for name in names
            ]),
            logger=logger,
        )

    def _unavailable_lines(self, logger):
        return [
            call for call in logger.info.call_args_list
            if 'unavailable' in str(call.args[0])
        ]

    def test_repeated_scans_log_the_missing_tool_once(self) -> None:
        import tempfile
        names = ('alpha-scanner', 'beta-scanner')
        logger = mock.MagicMock(spec=logging.Logger)
        service = self._service_with_unavailable_runners(logger, names)
        with tempfile.TemporaryDirectory() as workspace:
            for _ in range(4):
                service.scan_workspace(workspace)

        lines = self._unavailable_lines(logger)
        # Guard against the assertion passing vacuously: if no runner were
        # actually unavailable, "one line per runner" is trivially true at
        # zero and the test proves nothing.
        self.assertEqual(
            len(lines), len(names),
            'expected exactly one "unavailable" line per runner across 4 scans',
        )
        self.assertEqual({call.args[1] for call in lines}, set(names))

    def test_the_error_still_reaches_the_caller_every_scan(self) -> None:
        # Logging once must not mean REPORTING once — the result still has
        # to carry the failure, or a silent scan looks like a clean one.
        import tempfile
        logger = mock.MagicMock(spec=logging.Logger)
        service = self._service_with_unavailable_runners(logger, ('alpha-scanner',))
        with tempfile.TemporaryDirectory() as workspace:
            first = service.scan_workspace(workspace)
            second = service.scan_workspace(workspace)
        for report in (first, second):
            self.assertTrue(
                getattr(report, 'runner_errors', None),
                'the unavailable runner must still be reported to the caller',
            )
        self.assertEqual(len(self._unavailable_lines(logger)), 1)


if __name__ == '__main__':
    unittest.main()
