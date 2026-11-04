"""The setup wizard's "can kato reach your ticket system?" — run for real.

A real child process composes kato's real config from the saved settings and
runs kato's own validation query against a real (local) HTTP server standing
in for YouTrack. Nothing in this process's environment changes.
"""

from __future__ import annotations

import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from kato_core_lib.validation import tracker_connection_check as check

BASE = {
    'PATH': os.environ.get('PATH', ''),
    'KATO_ISSUE_PLATFORM': 'youtrack',
    'YOUTRACK_API_TOKEN': 'token',
    'YOUTRACK_PROJECT': 'PROJ',
    'YOUTRACK_ASSIGNEE': 'me',
}


class _Tracker(BaseHTTPRequestHandler):
    status = 200

    def do_GET(self):  # noqa: N802 - the http.server name
        type(self).seen.append(self.path)
        body = json.dumps([] if self.status == 200 else {'error': 'Unauthorized'}).encode()
        self.send_response(self.status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # quiet
        pass


class TrackerConnectionCheckTests(unittest.TestCase):
    def _serve(self, status: int) -> str:
        handler = type('Handler', (_Tracker,), {'status': status, 'seen': []})
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.handler = handler
        return f'http://127.0.0.1:{server.server_address[1]}'

    def test_a_tracker_that_answers_is_reached(self) -> None:
        url = self._serve(200)
        before = dict(os.environ)
        result = check.check_tracker_connection({**BASE, 'YOUTRACK_API_BASE_URL': url})
        self.assertEqual(result, {'ok': True, 'platform': 'youtrack', 'error': ''})
        self.assertTrue(any(path.startswith('/api/issues') for path in self.handler.seen))
        self.assertEqual(dict(os.environ), before)  # this process untouched

    def test_a_wrong_token_is_reported_in_full(self) -> None:
        url = self._serve(401)
        result = check.check_tracker_connection({**BASE, 'YOUTRACK_API_BASE_URL': url})
        self.assertFalse(result['ok'])
        self.assertIn('401', result['error'])

    def test_an_address_nothing_answers_at(self) -> None:
        result = check.check_tracker_connection({**BASE, 'YOUTRACK_API_BASE_URL': 'http://127.0.0.1:9'})
        self.assertFalse(result['ok'])
        self.assertIn('127.0.0.1', result['error'])

    def test_no_tracker_has_nothing_to_reach(self) -> None:
        result = check.check_tracker_connection({'PATH': BASE['PATH'], 'KATO_ISSUE_PLATFORM': 'local'})
        self.assertEqual(result, {'ok': True, 'platform': 'local', 'error': ''})

    def test_a_check_that_hangs_is_given_up_on(self) -> None:
        with patch.object(check.subprocess, 'run',
                          side_effect=check.subprocess.TimeoutExpired('x', 5)):
            result = check.check_tracker_connection(BASE, timeout=5)
        self.assertEqual(result['ok'], False)
        self.assertIn('no answer within 5 seconds', result['error'])

    def test_a_check_that_dies_says_why(self) -> None:
        died = check.subprocess.CompletedProcess([], 1, stdout='noise\n', stderr='Traceback\nboom: bad config\n')
        with patch.object(check.subprocess, 'run', return_value=died):
            self.assertIn('boom: bad config', check.check_tracker_connection(BASE)['error'])
        silent = check.subprocess.CompletedProcess([], 3, stdout='', stderr='')
        with patch.object(check.subprocess, 'run', return_value=silent):
            self.assertIn('exit code 3', check.check_tracker_connection({})['error'])


if __name__ == '__main__':
    unittest.main()
