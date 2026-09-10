"""Ctrl+C has to stop kato, and has to LOOK like it is stopping.

Reported as "i cannot ctrl+c to stop kato". Measured on the running process,
SIGINT did work — it just took ~9 seconds and printed nothing on the way, so
the operator reasonably concluded it had been ignored. Two separate defects
behind one symptom:

1. ``AgentService.shutdown`` waited for the worker pool to drain. That wait is
   unbounded (a git clone, a provider call, a whole agent run), so the
   graceful path reliably blew its entire grace period and exited on the
   TIMEOUT rather than finishing.
2. The only acknowledgement was a logger line, landing after the idle
   spinner had just repainted its countdown — so the terminal showed nothing
   at all between the keypress and the exit.
"""

from __future__ import annotations

import io
import signal
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from kato_core_lib.main import _register_shutdown_hook


def _app():
    return SimpleNamespace(
        logger=MagicMock(),
        service=MagicMock(),
        resume_prompt_watcher=None,
        comment_run_watcher=None,
    )


def _install(app):
    """Install the hook and hand back the SIGINT handler it registered."""
    handlers = {}

    def fake_signal(signum, handler):
        handlers[signum] = handler

    with patch('kato_core_lib.main.signal.signal', side_effect=fake_signal):
        _register_shutdown_hook(app)
    return handlers[signal.SIGINT]


class ShutdownFeedbackTests(unittest.TestCase):
    def test_the_first_ctrl_c_says_so_on_the_terminal_immediately(self) -> None:
        app = _app()
        handler = _install(app)
        buffer = io.StringIO()
        # Cleanup is slow (that is the whole point) — assert the notice is
        # written BEFORE it, not after.
        seen_before_cleanup = {}
        app.service.shutdown.side_effect = (
            lambda: seen_before_cleanup.setdefault('text', buffer.getvalue())
        )

        with patch('kato_core_lib.main.sys.stderr', buffer), \
                patch('kato_core_lib.main.supports_inline_status', return_value=False):
            with self.assertRaises(SystemExit):
                handler(signal.SIGINT, None)

        self.assertIn('stopping kato', seen_before_cleanup.get('text', ''))
        self.assertIn('Ctrl+C again', buffer.getvalue())

    def test_the_notice_clears_the_inline_spinner_first(self) -> None:
        # Otherwise it lands on top of "Idle · next scan in 42s" and the
        # operator reads a mangled half-line.
        app = _app()
        handler = _install(app)
        buffer = io.StringIO()
        with patch('kato_core_lib.main.sys.stderr', buffer), \
                patch('kato_core_lib.main.supports_inline_status', return_value=True):
            with self.assertRaises(SystemExit):
                handler(signal.SIGINT, None)
        # A carriage return + blanking run precedes the message.
        self.assertTrue(buffer.getvalue().startswith('\r'))
        self.assertIn('stopping kato', buffer.getvalue())

    def test_an_unwritable_stderr_never_turns_ctrl_c_into_a_crash(self) -> None:
        app = _app()
        handler = _install(app)
        broken = MagicMock()
        broken.write.side_effect = ValueError('I/O operation on closed file')
        with patch('kato_core_lib.main.sys.stderr', broken), \
                patch('kato_core_lib.main.supports_inline_status', return_value=False):
            with self.assertRaises(SystemExit):
                handler(signal.SIGINT, None)
        # ...and the shutdown still ran.
        app.service.shutdown.assert_called_once()

    def test_the_second_ctrl_c_forces_an_exit_without_cleanup(self) -> None:
        app = _app()
        handler = _install(app)
        buffer = io.StringIO()
        with patch('kato_core_lib.main.sys.stderr', buffer), \
                patch('kato_core_lib.main.supports_inline_status', return_value=False):
            with self.assertRaises(SystemExit):
                handler(signal.SIGINT, None)
            app.service.shutdown.reset_mock()
            with patch('kato_core_lib.main.os._exit') as forced:
                handler(signal.SIGINT, None)

        forced.assert_called_once_with(130)
        # The second signal must NOT re-enter the cleanup that is already
        # stuck — that is the thing it exists to escape.
        app.service.shutdown.assert_not_called()


class ShutdownDoesNotWaitForThePoolTests(unittest.TestCase):
    def test_the_worker_pool_is_dropped_not_drained(self) -> None:
        """The unbounded wait, pinned.

        ``wait=True`` blocks until every in-flight scan task finishes. Nothing
        is lost by not waiting: queued tasks never started, running ones are
        daemon threads, and an interrupted task is picked up by the next scan.
        """
        from kato_core_lib.data_layers.service.agent_service import AgentService

        service = AgentService.__new__(AgentService)
        service.logger = MagicMock()
        service._parallel_task_runner = MagicMock()
        service._implementation_service = MagicMock()
        service._testing_service = MagicMock()
        service._session_manager = None

        service.shutdown()

        service._parallel_task_runner.shutdown.assert_called_once_with(
            wait=False, cancel_futures=True,
        )

    def test_a_failing_pool_shutdown_still_stops_the_conversations(self) -> None:
        # Every step stays guarded: one failure must not strand the rest of
        # the cleanup, or Ctrl+C leaves agent subprocesses running.
        from kato_core_lib.data_layers.service.agent_service import AgentService

        service = AgentService.__new__(AgentService)
        service.logger = MagicMock()
        service._parallel_task_runner = MagicMock()
        service._parallel_task_runner.shutdown.side_effect = RuntimeError('boom')
        service._implementation_service = MagicMock()
        service._testing_service = MagicMock()
        service._session_manager = MagicMock()

        service.shutdown()

        service._implementation_service.stop_all_conversations.assert_called_once()
        service._session_manager.shutdown.assert_called_once()


if __name__ == '__main__':
    unittest.main()
