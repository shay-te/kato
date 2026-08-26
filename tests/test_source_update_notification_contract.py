"""The source-update notification is a cross-language string coupling.

The browser notification fires when the client's ``classifyStatusEntry``
matches a log line the SERVER writes. Nothing enforces that agreement — the
format string lives in Python and the regex in JavaScript — so a reworded log
line would silently stop the notification, with no test failing on either
side and no error anywhere.

This pins the two together.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SERVER = _ROOT / 'kato_core_lib' / 'data_layers' / 'service' / 'task_publish_service.py'
_CLIENT = _ROOT / 'webserver' / 'ui' / 'src' / 'utils' / 'classifyStatusEntry.js'


class SourceUpdateNotificationContractTests(unittest.TestCase):
    def test_the_server_still_logs_the_line_the_client_listens_for(self) -> None:
        server = _SERVER.read_text(encoding='utf-8')
        self.assertIn(
            'Mission %s: source update finished '
            '(%d updated, %d skipped, %d failed)',
            server,
            'the log line the SOURCE_UPDATE notification keys off was '
            'reworded; update the client regex in classifyStatusEntry.js '
            'in the same change',
        )

    def test_the_client_still_listens_for_it(self) -> None:
        client = _CLIENT.read_text(encoding='utf-8')
        self.assertIn(
            'source update finished', client,
            'classifyStatusEntry no longer recognises the server line',
        )

    def test_the_shapes_agree_field_for_field(self) -> None:
        """Four captures, in this order: task, updated, skipped, failed.

        The client reads ``m[2]`` as updated and ``m[4]`` as failed, so the
        counts must stay in that order — reordering them in the log line
        would swap the numbers in the notification with nothing failing.
        """
        client = _CLIENT.read_text(encoding='utf-8')
        self.assertIn(
            r'(\d+) updated, (\d+) skipped, (\d+) failed', client,
            'the client no longer parses three ordered counts',
        )
        self.assertIn(
            r'^Mission (\S+): source update finished', client,
            'the client no longer captures the task id first',
        )


if __name__ == '__main__':
    unittest.main()
