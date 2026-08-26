"""Ctrl+C must actually stop kato.

Reported as "i can't stop kato with CTRL+C in the terminal". The handler ran
cleanup INLINE, and cleanup terminates live agent subprocesses — so one that
ignored its terminate held the handler open forever. A second Ctrl+C did not
help either: the handler was already running, and the default SIGINT
behaviour had been replaced.

Two properties fix it: the graceful path is time-bounded, and a second signal
exits immediately without re-entering the cleanup that is already stuck.
"""

from __future__ import annotations

import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from kato_core_lib import main as kato_main


def _app(service=None, watcher=None):
    return SimpleNamespace(
        logger=MagicMock(),
        service=service,
        resume_prompt_watcher=watcher,
        comment_run_watcher=None,
    )


def _handler_for(app):
    """Install the hook and hand back the registered SIGINT handler."""
    captured = {}

    def fake_signal(signum, handler):
        captured[signum] = handler

    with patch.object(kato_main.signal, 'signal', fake_signal):
        kato_main._register_shutdown_hook(app)
    return captured[kato_main.signal.SIGINT]


class GracefulShutdownIsBoundedTests(unittest.TestCase):
    def test_a_clean_shutdown_still_runs_cleanup(self) -> None:
        service = MagicMock()
        watcher = MagicMock()
        handler = _handler_for(_app(service, watcher))

        with self.assertRaises(SystemExit):
            handler(2, None)

        service.shutdown.assert_called_once()
        watcher.stop.assert_called_once()

    def test_a_hung_cleanup_does_not_hold_the_process(self) -> None:
        """The reported bug: Ctrl+C that never returns."""
        service = MagicMock()
        service.shutdown.side_effect = lambda: time.sleep(30)
        app = _app(service)
        handler = _handler_for(app)

        started = time.monotonic()
        with patch.object(kato_main, 'SHUTDOWN_GRACE_SECONDS', 0.2):
            with self.assertRaises(SystemExit):
                handler(2, None)
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 5.0, 'shutdown waited on a hung cleanup')
        app.logger.warning.assert_called()

    def test_a_failing_cleanup_still_exits(self) -> None:
        service = MagicMock()
        service.shutdown.side_effect = RuntimeError('boom')
        handler = _handler_for(_app(service))
        with self.assertRaises(SystemExit):
            handler(2, None)

    def test_a_missing_service_is_fine(self) -> None:
        handler = _handler_for(_app(None))
        with self.assertRaises(SystemExit):
            handler(2, None)


class SecondSignalExitsImmediatelyTests(unittest.TestCase):
    def test_the_second_ctrl_c_force_exits(self) -> None:
        service = MagicMock()
        handler = _handler_for(_app(service))
        with self.assertRaises(SystemExit):
            handler(2, None)

        # The operator is still pressing Ctrl+C at a process that has not
        # died: no more waiting, and no re-entry into the stuck cleanup.
        with patch.object(kato_main.os, '_exit') as force_exit:
            handler(2, None)
        force_exit.assert_called_once_with(130)
        self.assertEqual(service.shutdown.call_count, 1)

    def test_the_second_signal_says_why(self) -> None:
        app = _app(MagicMock())
        handler = _handler_for(app)
        with self.assertRaises(SystemExit):
            handler(2, None)
        with patch.object(kato_main.os, '_exit'):
            handler(2, None)
        messages = ' '.join(
            str(call.args[0]) for call in app.logger.warning.call_args_list
        )
        self.assertIn('second shutdown signal', messages)

    def test_each_hook_tracks_its_own_state(self) -> None:
        # Two instances must not share a flag — a fresh hook's first signal
        # is still a graceful one.
        first = _handler_for(_app(MagicMock()))
        with self.assertRaises(SystemExit):
            first(2, None)
        second_service = MagicMock()
        second = _handler_for(_app(second_service))
        with self.assertRaises(SystemExit):
            second(2, None)
        second_service.shutdown.assert_called_once()


if __name__ == '__main__':
    unittest.main()
