"""Regression test for the status broadcaster attaching to the workflow logger.

The ``kato.workflow`` logger has ``propagate = False`` so its mission /
task lines don't double-print to stderr. That side effect used to mean
the planning UI's status bar showed nothing useful, because the
broadcaster handler was only attached to the root logger and never saw
the workflow records. The fix attaches the broadcaster to BOTH root and
``kato.workflow``. This test guards that behavior so a future change to
``install_status_broadcast_handler`` can't silently break the UI again.
"""

from __future__ import annotations

import logging
import unittest

from kato.helpers.status_broadcaster_utils import (
    StatusBroadcaster,
    install_status_broadcast_handler,
)


class WorkflowLoggerCaptureTests(unittest.TestCase):
    def test_workflow_logger_messages_reach_broadcaster(self) -> None:
        broadcaster = StatusBroadcaster()
        install_status_broadcast_handler(broadcaster)

        # Mirror what kato does: child loggers under ``kato.workflow``.
        # configure_logger sets ``kato.workflow.propagate = False``, so a
        # broadcaster on ROOT alone would never see these.
        from kato.helpers.logging_utils import configure_logger
        log = configure_logger('test_AgentService')

        log.info('Mission UNA-XYZ: starting mission: dummy')
        log.info('Mission UNA-XYZ: cloning repository: foo')

        messages = [entry.message for entry in broadcaster.recent()]
        self.assertIn(
            'Mission UNA-XYZ: starting mission: dummy', messages,
            'workflow-logger info must reach the broadcaster',
        )
        self.assertIn(
            'Mission UNA-XYZ: cloning repository: foo', messages,
            'workflow-logger info must reach the broadcaster',
        )

    def test_root_logger_messages_still_reach_broadcaster(self) -> None:
        broadcaster = StatusBroadcaster()
        install_status_broadcast_handler(broadcaster)

        logging.getLogger().info('plain root info line')
        messages = [entry.message for entry in broadcaster.recent()]
        self.assertIn(
            'plain root info line', messages,
            'root-logger info must still reach the broadcaster',
        )


if __name__ == '__main__':
    unittest.main()
