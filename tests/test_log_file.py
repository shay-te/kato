"""kato's log file: the console's lines, timestamped, plus every crash.

Reported from Windows: "where does it store the crash report?" — nowhere.
kato logged only to the console, so a crash under the desktop app (or in a
terminal closed since) left nothing behind.
"""
from __future__ import annotations

import logging
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from kato_core_lib.helpers import logging_utils
from kato_core_lib.helpers.logging_utils import (
    LOG_FILE_ENV,
    configure_logger,
    enable_log_file,
    log_file_path,
)


class _LogFileCase(unittest.TestCase):
    """A fresh log file per test; every handler and hook put back after."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / 'home' / '.kato' / 'logs' / 'kato.log'
        env = patch.dict(os.environ, {LOG_FILE_ENV: str(self.path)})
        env.start()
        self.addCleanup(env.stop)
        hooks = (sys.excepthook, threading.excepthook)
        self.addCleanup(self._put_back, hooks)
        # A handler another test (or main()) left on: this one starts without it.
        self._detach()

    def _put_back(self, hooks) -> None:
        sys.excepthook, threading.excepthook = hooks
        self._detach()

    @staticmethod
    def _detach() -> None:
        for name in ('', logging_utils._WORKFLOW_LOGGER_PREFIX, logging_utils._CRASH_LOGGER_NAME):
            logger = logging.getLogger(name)
            for handler in list(logger.handlers):
                if handler.get_name() == logging_utils._FILE_HANDLER_NAME:
                    logger.removeHandler(handler)
                    handler.close()

    def written(self) -> str:
        return self.path.read_text(encoding='utf-8') if self.path.exists() else ''


class LogFileTests(_LogFileCase):

    def test_it_lives_in_katos_folder_and_takes_the_override(self) -> None:
        self.assertEqual(log_file_path(), self.path)
        with patch.dict(os.environ, {LOG_FILE_ENV: ''}):
            self.assertEqual(log_file_path(), Path.home() / '.kato' / 'logs' / 'kato.log')

    def test_the_consoles_lines_are_on_file_with_when_and_how_bad(self) -> None:
        self.assertEqual(enable_log_file(), self.path)
        configure_logger('scan').info('picked up UNA-1')
        logging.getLogger('urllib3').warning('connection pool is full')
        logging.getLogger('urllib3').info('starting a connection')  # below the console level
        lines = self.written().splitlines()
        self.assertEqual(len(lines), 2, lines)
        self.assertRegex(
            lines[0],
            rf'^\d{{4}}-\d\d-\d\d \d\d:\d\d:\d\d,\d+ \[{os.getpid()}\] INFO kato\.workflow\.scan: picked up UNA-1$',
        )
        self.assertRegex(lines[1], r' WARNING urllib3: connection pool is full$')

    def test_turning_it_on_twice_keeps_one_file_and_one_handler(self) -> None:
        enable_log_file()
        self.assertEqual(enable_log_file(), self.path)
        configure_logger('scan').info('once')
        self.assertEqual(self.written().count('once'), 1)

    def test_a_folder_it_cannot_write_is_said_and_kato_runs_on(self) -> None:
        self.path.parent.parent.mkdir(parents=True)
        self.path.parent.write_text('a file where the logs folder should be', encoding='utf-8')
        with self.assertLogs('kato.workflow.logging', level='WARNING') as logs:
            self.assertIsNone(enable_log_file())
        self.assertIn('kato could not open its log file', logs.output[0])


class CrashReportTests(_LogFileCase):

    def test_an_unhandled_exception_is_on_file_with_its_traceback(self) -> None:
        previous = Mock()
        sys.excepthook = previous
        enable_log_file()
        try:
            raise ValueError('settings.json is not JSON')
        except ValueError:
            info = sys.exc_info()
        sys.excepthook(*info)
        written = self.written()
        self.assertIn('CRITICAL kato.crash: kato crashed', written)
        self.assertIn('Traceback (most recent call last)', written)
        self.assertIn('ValueError: settings.json is not JSON', written)
        previous.assert_called_once_with(*info)  # the console still prints it

    def test_ctrl_c_is_not_a_crash(self) -> None:
        previous = Mock()
        sys.excepthook = previous
        enable_log_file()
        sys.excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)
        self.assertNotIn('crashed', self.written())
        previous.assert_called_once()

    def test_a_crashed_background_thread_is_on_file_too(self) -> None:
        threading.excepthook = Mock()
        enable_log_file()

        def scan():
            raise RuntimeError('the git host refused the token')

        worker = threading.Thread(target=scan, name='task-scan')
        worker.start()
        worker.join()
        written = self.written()
        self.assertIn('kato thread task-scan crashed', written)
        self.assertIn('RuntimeError: the git host refused the token', written)

    def test_a_thread_ended_on_purpose_is_not_a_crash(self) -> None:
        threading.excepthook = Mock()
        enable_log_file()
        worker = threading.Thread(target=sys.exit, name='done')
        worker.start()
        worker.join()
        self.assertNotIn('crashed', self.written())


class MainKeepsALogFileTests(_LogFileCase):

    def test_main_turns_it_on_once_the_folder_is_its_own_and_says_where(self) -> None:
        from kato_core_lib import main as main_module

        class _StopHere(Exception):
            pass

        logger = Mock()
        with patch.object(main_module, 'configure_logger', return_value=logger), \
                patch.object(main_module, 'hold_instance_lock', return_value=''), \
                patch.object(main_module, 'collect_config_errors', side_effect=_StopHere), \
                self.assertRaises(_StopHere):
            main_module.main(Mock())
        logger.info.assert_any_call('kato logs to %s', self.path)
        self.assertTrue(self.path.exists())

    def test_hydra_lets_a_crash_through_to_the_log_file(self) -> None:
        # Otherwise hydra prints a trimmed traceback and exits: sys.excepthook
        # — and so the log file — never hears of a crash under `kato up`.
        from kato_core_lib import main as main_module  # noqa: F401 — sets it on import
        self.assertEqual(os.environ.get('HYDRA_FULL_ERROR'), '1')


class CrashHooksAreInstalledOnceTests(_LogFileCase):

    def test_turning_the_file_on_again_does_not_write_each_crash_twice(self) -> None:
        sys.excepthook = Mock()
        enable_log_file()
        self._detach()          # a test (or a restart) took the handler off
        enable_log_file()
        try:
            raise ValueError('once')
        except ValueError:
            sys.excepthook(*sys.exc_info())
        self.assertEqual(self.written().count('kato crashed'), 1)

    def test_levels_are_the_ones_the_console_took_at_boot(self) -> None:
        enable_log_file()
        with patch.dict(os.environ, {'KATO_LOG_LEVEL': 'debug'}):
            logging.getLogger('urllib3').debug('a setting loaded later must not open the file up')
        self.assertNotIn('must not open', self.written())


if __name__ == '__main__':
    unittest.main()
