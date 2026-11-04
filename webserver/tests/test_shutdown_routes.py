"""The UI's Shutdown: which agents will come back, then the Ctrl+C path."""

from __future__ import annotations

import signal
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from kato_webserver.app import create_app
from kato_webserver import shutdown_routes


class _Sessions(object):
    def list_records(self):
        return [SimpleNamespace(task_id='UNA-1'), SimpleNamespace(task_id='UNA-2')]

    def get_session(self, task_id):
        return SimpleNamespace(is_alive=True, is_working=task_id == 'UNA-1',
                               pending_control_request_tool=lambda: '')


class ShutdownRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_app(session_manager=_Sessions(), workspace_manager=None)
        self.stopped = threading.Event()
        self.app.config['REQUEST_SHUTDOWN'] = self.stopped.set
        self.client = self.app.test_client()

    def test_the_preview_names_the_agents_that_will_be_resumed(self) -> None:
        self.assertEqual(self.client.get('/api/shutdown').get_json(), {'running': ['UNA-1']})
        self.assertFalse(self.stopped.is_set())

    def test_shutting_down_answers_first_then_stops(self) -> None:
        with patch.object(shutdown_routes, 'ANSWER_GRACE_SECONDS', 0.05):
            answer = self.client.post('/api/shutdown')
        self.assertEqual(answer.status_code, 202)
        self.assertEqual(answer.get_json(), {'running': ['UNA-1'], 'shutting_down': True})
        self.assertTrue(self.stopped.wait(5))

    def test_by_default_it_is_kato_s_own_ctrl_c(self) -> None:
        with patch.object(shutdown_routes.signal, 'raise_signal') as raise_signal:
            shutdown_routes.press_ctrl_c()
        raise_signal.assert_called_once_with(signal.SIGINT)
        pressed = threading.Event()
        self.app.config['REQUEST_SHUTDOWN'] = None
        # Waited for INSIDE the patch: a timer firing after it would send a
        # real SIGINT to the test runner.
        with patch.object(shutdown_routes, 'ANSWER_GRACE_SECONDS', 0.0), \
                patch.object(shutdown_routes, 'press_ctrl_c', side_effect=pressed.set):
            self.client.post('/api/shutdown')
            self.assertTrue(pressed.wait(5))

    def test_another_site_cannot_shut_kato_down(self) -> None:
        answer = self.client.post('/api/shutdown', headers={'Origin': 'https://evil.example', 'Sec-Fetch-Site': 'cross-site'})
        self.assertEqual(answer.status_code, 403)
        self.assertFalse(self.stopped.wait(0.2))


if __name__ == '__main__':
    unittest.main()
