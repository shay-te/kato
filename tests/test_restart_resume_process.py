"""Out of process: what a real stop leaves on disk for the next start.

A child runs kato's own shutdown hook with the recorder and two working agents.
Ctrl+C (SIGINT) must record them BEFORE the teardown stops them — the teardown
here clears them and then lingers, which a recorder that kept writing would
turn into "nothing was running". SIGKILL cannot be caught at all: the list on
disk is the recorder's last write, so it must already be there.
"""

from __future__ import annotations

import json
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

_CHILD = '''
import sys, time
from types import SimpleNamespace
sys.path.insert(0, {root!r})
from kato_core_lib import main as kato_main
from kato_core_lib.data_layers.service.restart_resume.recorder import RunningAgentsRecorder
from kato_core_lib.data_layers.service.restart_resume.resume_store import ResumeStore

class Session(object):
    is_alive = True
    is_working = True
    def pending_control_request_tool(self):
        return ''

class Sessions(object):
    def __init__(self):
        self.live = {{'UNA-1': Session(), 'UNA-2': Session()}}
    def list_records(self):
        return [SimpleNamespace(task_id=task_id) for task_id in list(self.live)]
    def get_session(self, task_id):
        return self.live.get(task_id)

sessions = Sessions()
recorder = RunningAgentsRecorder(session_manager=sessions, store=ResumeStore({path!r}), interval=0.02)
recorder.start()

def stop_every_agent():
    sessions.live.clear()
    time.sleep(0.3)  # long enough for a recorder still writing to write "nothing"

quiet = lambda *args, **kwargs: None
app = SimpleNamespace(
    logger=SimpleNamespace(info=quiet, warning=quiet, exception=quiet, debug=quiet),
    service=SimpleNamespace(shutdown=stop_every_agent),
    running_agents_recorder=recorder,
)
kato_main._register_shutdown_hook(app)
print('ready', flush=True)
while True:
    time.sleep(0.05)
'''


class StopLeavesTheListTests(unittest.TestCase):
    def _child(self, path: Path) -> subprocess.Popen:
        child = subprocess.Popen(
            [sys.executable, '-c', _CHILD.format(root=str(REPO_ROOT), path=str(path))],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, text=True,
        )
        self.addCleanup(self._reap, child)
        self.assertEqual((child.stdout.readline() or '').strip(), 'ready')
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if path.exists() and json.loads(path.read_text())['task_ids'] == ['UNA-1', 'UNA-2']:
                return child
            time.sleep(0.02)
        self.fail('the recorder never wrote the working agents')

    @staticmethod
    def _reap(child: subprocess.Popen) -> None:
        if child.poll() is None:
            child.kill()
            child.wait()
        child.stdout.close()

    def _path(self) -> Path:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        return Path(folder.name) / 'resume_agents.json'

    def test_ctrl_c_records_the_working_agents_before_they_stop(self) -> None:
        path = self._path()
        child = self._child(path)
        child.send_signal(signal.SIGINT)
        child.wait(timeout=30)
        self.assertEqual(json.loads(path.read_text())['task_ids'], ['UNA-1', 'UNA-2'])

    @unittest.skipIf(sys.platform == 'win32', 'SIGKILL is POSIX')
    def test_a_kill_that_cannot_be_caught_leaves_the_last_recorded_list(self) -> None:
        path = self._path()
        child = self._child(path)
        child.send_signal(signal.SIGKILL)
        child.wait(timeout=30)
        self.assertEqual(json.loads(path.read_text())['task_ids'], ['UNA-1', 'UNA-2'])



_BUTTON_CHILD = _CHILD.replace("print('ready', flush=True)", """
import threading
from werkzeug.serving import make_server
from kato_webserver.app import create_app
server = make_server('127.0.0.1', 0, create_app(session_manager=sessions, workspace_manager=None))
threading.Thread(target=server.serve_forever, daemon=True).start()
print(server.server_port, flush=True)
print('ready', flush=True)""")


class ShutdownButtonTests(unittest.TestCase):
    """The UI's button: a real HTTP request, the real Ctrl+C path."""

    def test_the_button_stops_kato_and_leaves_the_working_agents_listed(self) -> None:
        import urllib.request
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / 'resume_agents.json'
        child = subprocess.Popen(
            [sys.executable, '-c', _BUTTON_CHILD.format(root=str(REPO_ROOT), path=str(path))],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, text=True,
            env={**__import__('os').environ, 'PYTHONPATH': f"{REPO_ROOT}{__import__('os').pathsep}{REPO_ROOT / 'webserver'}"},
        )
        self.addCleanup(StopLeavesTheListTests._reap, child)
        port = int(child.stdout.readline().strip())
        self.assertEqual(child.stdout.readline().strip(), 'ready')
        request = urllib.request.Request(f'http://127.0.0.1:{port}/api/shutdown', data=b'{}', method='POST',
                                         headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=10) as answer:
            self.assertEqual(answer.status, 202)
            self.assertEqual(json.loads(answer.read())['running'], ['UNA-1', 'UNA-2'])
        self.assertIsNotNone(child.wait(timeout=30))
        self.assertEqual(json.loads(path.read_text())['task_ids'], ['UNA-1', 'UNA-2'])


if __name__ == '__main__':
    unittest.main()
