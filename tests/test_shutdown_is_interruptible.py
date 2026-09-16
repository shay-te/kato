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


def _run_handler(handler, signum=2, *, expect_code=0):
    """Call the handler and assert the exit it asked for.

    The graceful path ends at ``main._exit_now``, NOT ``raise SystemExit``:
    unwinding the main thread does not end a process whose ThreadPoolExecutor
    workers are still running (they are non-daemon and joined at interpreter
    exit), which is why Ctrl+C still appeared to do nothing while a scan task
    was in flight. Patched here so calling the handler in-process does not
    take the test runner down with it.
    """
    with patch.object(kato_main, '_exit_now') as exit_now:
        handler(signum, None)
    exit_now.assert_called_once_with(expect_code)


class GracefulShutdownIsBoundedTests(unittest.TestCase):
    def test_a_clean_shutdown_still_runs_cleanup(self) -> None:
        service = MagicMock()
        watcher = MagicMock()
        handler = _handler_for(_app(service, watcher))

        _run_handler(handler)

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
            _run_handler(handler)
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 5.0, 'shutdown waited on a hung cleanup')
        app.logger.warning.assert_called()

    def test_a_failing_cleanup_still_exits(self) -> None:
        service = MagicMock()
        service.shutdown.side_effect = RuntimeError('boom')
        handler = _handler_for(_app(service))
        _run_handler(handler)

    def test_a_missing_service_is_fine(self) -> None:
        handler = _handler_for(_app(None))
        _run_handler(handler)


class TheExitFlushesFirstTests(unittest.TestCase):
    """``os._exit`` skips ``logging.shutdown()`` — flush before leaving.

    The abrupt exit is deliberate (a non-daemon pool worker would otherwise
    hold the process open), but it also means nothing else gets to run. A
    buffered handler would swallow the last lines of the shutdown, including
    whatever cleanup complained about.
    """

    def test_handlers_are_flushed_before_the_process_ends(self) -> None:
        order: list[str] = []
        with patch.object(kato_main.logging, 'shutdown',
                          side_effect=lambda: order.append('flush')), \
                patch.object(kato_main.os, '_exit',
                             side_effect=lambda code: order.append(f'exit:{code}')):
            kato_main._exit_now(0)
        self.assertEqual(order, ['flush', 'exit:0'])

    def test_a_failing_flush_never_blocks_the_exit(self) -> None:
        with patch.object(kato_main.logging, 'shutdown',
                          side_effect=RuntimeError('handler already closed')), \
                patch.object(kato_main.os, '_exit') as forced:
            kato_main._exit_now(0)
        forced.assert_called_once_with(0)


class SecondSignalExitsImmediatelyTests(unittest.TestCase):
    def test_the_second_ctrl_c_force_exits(self) -> None:
        service = MagicMock()
        handler = _handler_for(_app(service))
        _run_handler(handler)

        # The operator is still pressing Ctrl+C at a process that has not
        # died: no more waiting, and no re-entry into the stuck cleanup.
        with patch.object(kato_main.os, '_exit') as force_exit:
            handler(2, None)
        force_exit.assert_called_once_with(130)
        self.assertEqual(service.shutdown.call_count, 1)

    def test_the_second_signal_says_why(self) -> None:
        app = _app(MagicMock())
        handler = _handler_for(app)
        _run_handler(handler)
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
        _run_handler(first)
        second_service = MagicMock()
        second = _handler_for(_app(second_service))
        _run_handler(second)
        second_service.shutdown.assert_called_once()


if __name__ == '__main__':
    unittest.main()
