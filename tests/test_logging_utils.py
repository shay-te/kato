from __future__ import annotations

import io
import logging
import os
import sys
import unittest
from unittest.mock import patch

from kato_core_lib.helpers import logging_utils


class LoggingUtilsTests(unittest.TestCase):
    def tearDown(self) -> None:
        logging_utils._LOGGING_CONFIGURED = False
        root_logger = logging.getLogger()
        workflow_logger = logging.getLogger(logging_utils._WORKFLOW_LOGGER_PREFIX)
        root_logger.handlers = [
            handler
            for handler in root_logger.handlers
            if handler.get_name() != logging_utils._ROOT_HANDLER_NAME
        ]
        workflow_logger.handlers = [
            handler
            for handler in workflow_logger.handlers
            if handler.get_name() != logging_utils._WORKFLOW_HANDLER_NAME
        ]
        workflow_logger.propagate = True
        workflow_logger.setLevel(logging.NOTSET)

    def test_configure_logger_defaults_to_warning_dependencies_and_info_workflow(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            logger = logging_utils.configure_logger('AgentService')

        self.assertEqual(logger.name, 'kato.workflow.AgentService')
        root_handler = self._named_handler(logging.getLogger(), logging_utils._ROOT_HANDLER_NAME)
        workflow_handler = self._named_handler(
            logging.getLogger(logging_utils._WORKFLOW_LOGGER_PREFIX),
            logging_utils._WORKFLOW_HANDLER_NAME,
        )
        self.assertIsNotNone(root_handler)
        self.assertIsNotNone(workflow_handler)
        self.assertEqual(root_handler.level, logging.WARNING)
        self.assertEqual(workflow_handler.level, logging.INFO)
        self.assertEqual(workflow_handler.formatter._fmt, '%(message)s')
        self.assertIsInstance(
            workflow_handler,
            logging.StreamHandler,
        )

    def test_configure_logger_uses_configured_dependency_and_workflow_levels(self) -> None:
        with patch.dict(
            os.environ,
            {
                'KATO_LOG_LEVEL': 'error',
                'KATO_WORKFLOW_LOG_LEVEL': 'debug',
            },
            clear=False,
        ):
            logging_utils.configure_logger('AgentService')

        root_handler = self._named_handler(logging.getLogger(), logging_utils._ROOT_HANDLER_NAME)
        workflow_handler = self._named_handler(
            logging.getLogger(logging_utils._WORKFLOW_LOGGER_PREFIX),
            logging_utils._WORKFLOW_HANDLER_NAME,
        )
        self.assertEqual(root_handler.level, logging.ERROR)
        self.assertEqual(workflow_handler.level, logging.DEBUG)

    def test_configure_logger_falls_back_to_defaults_for_invalid_levels(self) -> None:
        with patch.dict(
            os.environ,
            {
                'KATO_LOG_LEVEL': 'LOUD',
                'KATO_WORKFLOW_LOG_LEVEL': 'CHATTER',
            },
            clear=False,
        ):
            logging_utils.configure_logger('AgentService')

        root_handler = self._named_handler(logging.getLogger(), logging_utils._ROOT_HANDLER_NAME)
        workflow_handler = self._named_handler(
            logging.getLogger(logging_utils._WORKFLOW_LOGGER_PREFIX),
            logging_utils._WORKFLOW_HANDLER_NAME,
        )
        self.assertEqual(root_handler.level, logging.WARNING)
        self.assertEqual(workflow_handler.level, logging.INFO)

    @staticmethod
    def _named_handler(logger: logging.Logger, name: str) -> logging.Handler | None:
        for handler in logger.handlers:
            if handler.get_name() == name:
                return handler
        return None


class AgentWorkflowRootResetTests(unittest.TestCase):
    """agent_core_lib's shared logger root defaults to the generic
    ``agent.workflow``; importing kato's logging_utils re-roots it under
    ``kato.workflow`` so transport (Claude/Codex/OpenHands) loggers — which use
    agent_core_lib's configure_logger — parent under kato's namespace. This
    keeps them under kato's status-broadcast handler + KATO_WORKFLOW_LOG_LEVEL
    control; guards the regression where they'd orphan to ``agent.workflow``
    and the planning UI status bar would go silent for transport events.
    """

    def test_importing_kato_logging_utils_reroots_agent_core_lib(self) -> None:
        from agent_core_lib.agent_core_lib.helpers.logging_utils import (
            configure_logger as agent_configure_logger,
            get_workflow_root,
        )
        # ``from kato_core_lib.helpers import logging_utils`` at module top runs
        # set_workflow_root('kato.workflow') at import time.
        self.assertEqual(get_workflow_root(), 'kato.workflow')
        # A transport-style logger built via agent_core_lib now lands under
        # kato's namespace (a child of the status-broadcast target).
        self.assertEqual(
            agent_configure_logger('ClaudeCliClient').name,
            'kato.workflow.ClaudeCliClient',
        )


class StreamEncodingHardeningTests(unittest.TestCase):
    """A non-ASCII character must never take a request down.

    Reported from a Windows host: every chat message produced

        UnicodeEncodeError: 'charmap' codec can't encode character '→'

    and Claude stopped responding on every task. Those were ONE bug.
    ``logging.Handler.handleError`` writes the failing record's traceback to
    ``sys.stderr`` and catches only ``OSError``, so when that write hits the
    same unencodable character the error propagates out of ``emit()`` — out
    of ``logger.info()`` — and into the caller. In kato's case the caller is
    ``post_message``, so the POST 500s and the message never reaches the
    agent.

    Fixing only kato's format strings would not have been enough: the
    ARGUMENTS are operator data (ticket summaries, branch names, agent
    output) and can hold anything.
    """

    def setUp(self) -> None:
        self._stdout, self._stderr = sys.stdout, sys.stderr
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        sys.stdout, sys.stderr = self._stdout, self._stderr

    @staticmethod
    def _cp1252_stream() -> io.TextIOWrapper:
        # What a redirected stream on Windows actually gives you.
        return io.TextIOWrapper(io.BytesIO(), encoding='cp1252', errors='strict')

    def _log_arrow(self) -> None:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter('%(message)s'))
        logger = logging.getLogger('kato-encoding-probe')
        logger.handlers = [handler]
        logger.propagate = False
        logger.setLevel(logging.INFO)
        logger.info(
            'task %s: chat message from the %s tab → running on %s',
            'UNA-3070', 'claude', 'claude',
        )

    def test_without_hardening_the_error_escapes_into_the_caller(self) -> None:
        # Pins the CAUSE. If a future logging change stops it escaping, this
        # test failing is the signal to revisit the whole rationale above.
        sys.stderr = self._cp1252_stream()
        with self.assertRaises(UnicodeEncodeError):
            self._log_arrow()

    def test_hardening_lets_the_log_call_return(self) -> None:
        sys.stderr = self._cp1252_stream()
        logging_utils.harden_stream_encoding()
        self._log_arrow()  # must not raise

    def test_the_line_is_actually_written_not_just_swallowed(self) -> None:
        # Degrading to a silent no-op would also "not raise" — the operator
        # still has to be able to read the line.
        buffer = io.BytesIO()
        sys.stderr = io.TextIOWrapper(buffer, encoding='cp1252', errors='strict')
        logging_utils.harden_stream_encoding()
        self._log_arrow()
        sys.stderr.flush()
        written = buffer.getvalue().decode('utf-8', errors='replace')
        self.assertIn('UNA-3070', written)
        self.assertIn('→', written)

    def test_non_ascii_in_the_ARGUMENTS_is_survivable_too(self) -> None:
        # The reason a format-string sweep could not have fixed this.
        buffer = io.BytesIO()
        sys.stderr = io.TextIOWrapper(buffer, encoding='cp1252', errors='strict')
        logging_utils.harden_stream_encoding()
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter('%(message)s'))
        logger = logging.getLogger('kato-encoding-args-probe')
        logger.handlers = [handler]
        logger.propagate = False
        logger.setLevel(logging.INFO)
        logger.info('task %s: %s', 'UNA-1', 'café — “quoted” ✓')
        sys.stderr.flush()
        self.assertIn('café', buffer.getvalue().decode('utf-8', errors='replace'))

    def test_a_stream_without_reconfigure_is_tolerated(self) -> None:
        # pytest capture / a custom tee replaces the stream with an object
        # that has no ``reconfigure``. That must not raise at startup.
        sys.stdout = io.StringIO()
        sys.stderr = io.StringIO()
        logging_utils.harden_stream_encoding()  # must not raise

    def test_configure_logger_hardens_on_first_call(self) -> None:
        # Every entry point goes through configure_logger, so nothing has to
        # remember to call the hardening itself.
        sys.stderr = self._cp1252_stream()
        logging_utils._LOGGING_CONFIGURED = False
        self.addCleanup(setattr, logging_utils, '_LOGGING_CONFIGURED', False)
        with patch.object(logging_utils, 'harden_stream_encoding') as harden:
            logging_utils.configure_logger('probe')
        harden.assert_called_once_with()
