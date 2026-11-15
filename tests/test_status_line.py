"""Kato's one terminal status line: a scan spins in place, never two new lines.

Operator, 2026-11-15, pasting a terminal of "Scanning for new tasks and
reviews" / "Scan complete" pairs: "no need to print it millions of times, use
a rotating progress".
"""

from __future__ import annotations

import io
import logging
import threading
import types
import unittest
from unittest.mock import Mock, patch

from kato_core_lib import main as main_module
from kato_core_lib.helpers import logging_utils
from kato_core_lib.helpers.shell_status_utils import (
    StatusLineAwareHandler,
    clear_inline_status,
    show_inline_status,
    spinning_status,
)


class _Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


def _screen(raw: str) -> list[str]:
    """What a terminal shows for ``raw``: ``\\r`` rewrites the line it is on."""
    lines = ['']
    for char in raw:
        if char == '\n':
            lines.append('')
        elif char == '\r':
            lines[-1] = ''
        else:
            lines[-1] += char
    return lines


class StatusLineTests(unittest.TestCase):
    def test_a_shorter_text_wipes_what_a_longer_one_left(self) -> None:
        terminal = _Terminal()
        show_inline_status('Scanning for new tasks and reviews /', terminal)
        show_inline_status('Idle · 5', terminal)
        self.assertEqual(terminal.getvalue().rsplit('\r', 1)[-1].rstrip(), 'Idle · 5')
        self.assertTrue(terminal.getvalue().rsplit('\r', 1)[-1].startswith('Idle · 5   '))
        clear_inline_status(terminal)

    def test_a_scan_spins_on_one_line_and_leaves_it_empty(self) -> None:
        terminal = _Terminal()
        drew = threading.Event()
        real_show = show_inline_status

        def counting_show(text, stream=None):
            real_show(text, stream)
            drew.set()

        with patch('kato_core_lib.helpers.shell_status_utils.show_inline_status', side_effect=counting_show):
            with spinning_status('Scanning', stream=terminal, frame_seconds=0.01):
                self.assertTrue(drew.wait(2))
        raw = terminal.getvalue()
        self.assertIn('Scanning /', raw)
        self.assertNotIn('\n', raw)  # never a new line
        self.assertEqual(_screen(raw), [''])  # and nothing left on it

    def test_a_redirected_stream_gets_nothing(self) -> None:
        stream = io.StringIO()  # not a terminal
        ran = []
        with spinning_status('Scanning', stream=stream):
            ran.append(True)
        self.assertEqual((ran, stream.getvalue()), ([True], ''))

    def test_a_log_line_prints_on_its_own_line_never_glued_to_the_spinner(self) -> None:
        terminal = _Terminal()
        handler = StatusLineAwareHandler(terminal)
        handler.setFormatter(logging.Formatter('%(message)s'))
        logger = logging.getLogger('kato.test.status_line')
        logger.propagate = False
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)
        show_inline_status('Scanning for new tasks and reviews /', terminal)
        logger.warning('task KAN-1 picked up')
        self.assertEqual(_screen(terminal.getvalue())[0].strip(), 'task KAN-1 picked up')
        # The line is free: a log line with no spinner showing is printed as is.
        before = len(terminal.getvalue())
        logger.warning('second line')
        self.assertEqual(terminal.getvalue()[before:], 'second line\n')

    def test_the_console_handlers_are_status_line_aware(self) -> None:
        saved = logging_utils._LOGGING_CONFIGURED
        root, workflow = logging.getLogger(), logging.getLogger(logging_utils._WORKFLOW_LOGGER_PREFIX)
        saved_handlers = (list(root.handlers), list(workflow.handlers), workflow.propagate, workflow.level)

        def restore() -> None:
            logging_utils._LOGGING_CONFIGURED = saved
            root.handlers, workflow.handlers = saved_handlers[0], saved_handlers[1]
            workflow.propagate, workflow.level = saved_handlers[2], saved_handlers[3]

        self.addCleanup(restore)
        root.handlers = [h for h in root.handlers if h.get_name() != logging_utils._ROOT_HANDLER_NAME]
        workflow.handlers = [h for h in workflow.handlers if h.get_name() != logging_utils._WORKFLOW_HANDLER_NAME]
        logging_utils._LOGGING_CONFIGURED = False
        logging_utils.configure_logger('StatusLine')
        for logger, name in ((root, logging_utils._ROOT_HANDLER_NAME), (workflow, logging_utils._WORKFLOW_HANDLER_NAME)):
            [handler] = [h for h in logger.handlers if h.get_name() == name]
            self.assertIsInstance(handler, StatusLineAwareHandler)


class ScanLoopPrintsNoLinesTests(unittest.TestCase):
    def _run(self, job_run=None):
        app = types.SimpleNamespace(logger=Mock())
        job = Mock()
        job.run.side_effect = job_run
        published = []
        with patch.object(main_module, 'ProcessAssignedTasksJob', return_value=job), \
             patch.object(main_module._STATUS_BROADCASTER, 'publish',
                          side_effect=lambda **entry: published.append(entry)), \
             patch.object(main_module, 'spinning_status') as spinning:
            main_module._run_task_scan_loop(app, scan_interval_seconds=60.0, sleep_fn=Mock(), max_cycles=1)
        return app, published, spinning

    def test_a_scan_logs_nothing_and_tells_only_the_ui(self) -> None:
        app, published, spinning = self._run()
        app.logger.info.assert_not_called()
        self.assertEqual(
            [entry['message'] for entry in published],
            ['Scanning for new tasks and reviews', 'Scan complete'],
        )
        self.assertEqual({entry['logger_name'] for entry in published}, {'kato.workflow.scan'})
        spinning.assert_called_once_with('Scanning for new tasks and reviews')

    def test_a_failed_scan_still_logs_its_warning_line(self) -> None:
        app, published, _spinning = self._run(job_run=RuntimeError('tracker down'))
        app.logger.warning.assert_called_once_with('task scan failed; retrying in %s seconds', 60.0)
        self.assertEqual([entry['message'] for entry in published], ['Scanning for new tasks and reviews'])


if __name__ == '__main__':
    unittest.main()
