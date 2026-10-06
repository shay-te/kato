"""``run_cancellable``: ``subprocess.run``'s contract, plus a Stop that works.

Driven against real child processes (``python -c``), because the whole point is
how a real process — and the process it started — dies. Nothing is mocked.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from agent_core_lib.agent_core_lib.helpers.cancellable_process import (
    ProcessCancelled,
    run_cancellable,
)
from agent_core_lib.agent_core_lib.helpers.process_liveness import pid_alive

_PY = sys.executable


def _run(code: str, *, input_text: str = '', timeout: float | None = 30,
         cancel: threading.Event | None = None) -> subprocess.CompletedProcess:
    return run_cancellable(
        [_PY, '-c', code],
        input_text=input_text,
        cwd=None,
        env=None,
        timeout_seconds=timeout,
        cancel_event=cancel or threading.Event(),
        poll_seconds=0.05,
    )


class RunsToCompletionTests(unittest.TestCase):

    def test_stdin_goes_in_and_stdout_comes_back(self) -> None:
        done = _run(
            'import sys; data = sys.stdin.read(); print(data.upper()); '
            'print("warn", file=sys.stderr)',
            input_text='review this diff',
        )
        self.assertEqual(done.returncode, 0)
        self.assertEqual(done.stdout.strip(), 'REVIEW THIS DIFF')
        self.assertEqual(done.stderr.strip(), 'warn')

    def test_a_non_zero_exit_is_returned_not_raised(self) -> None:
        # Same as ``subprocess.run(check=False)``: the caller reads the code.
        done = _run('import sys; sys.exit(3)')
        self.assertEqual(done.returncode, 3)

    def test_large_output_does_not_deadlock(self) -> None:
        done = _run('print("x" * 2_000_000)')
        self.assertEqual(len(done.stdout.strip()), 2_000_000)

    def test_no_timeout_means_wait_for_the_exit(self) -> None:
        done = _run('import time; time.sleep(0.3); print("ok")', timeout=None)
        self.assertEqual(done.stdout.strip(), 'ok')

    def test_a_missing_binary_raises_like_subprocess_run(self) -> None:
        with self.assertRaises(OSError):
            run_cancellable(
                ['/no/such/binary-for-this-test'],
                input_text='', cwd=None, env=None, timeout_seconds=5,
                cancel_event=threading.Event(),
            )


class StopsEarlyTests(unittest.TestCase):

    def test_timeout_raises_timeout_expired_and_kills_the_child(self) -> None:
        started = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            _run('import time; time.sleep(30)', timeout=0.5)
        self.assertLess(time.monotonic() - started, 10)

    def test_cancel_raises_process_cancelled_promptly(self) -> None:
        cancel = threading.Event()
        threading.Timer(0.3, cancel.set).start()
        started = time.monotonic()
        with self.assertRaises(ProcessCancelled):
            _run('import time; time.sleep(30)', cancel=cancel)
        self.assertLess(time.monotonic() - started, 10)

    def test_an_already_set_cancel_never_lets_it_finish(self) -> None:
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(ProcessCancelled):
            _run('import time; time.sleep(30)', cancel=cancel)

    @unittest.skipIf(os.name == 'nt', 'process groups are POSIX')
    def test_cancel_takes_the_grandchild_too(self) -> None:
        # The agent CLI starts tools of its own; a Stop that leaves those
        # running is not a Stop.
        with tempfile.TemporaryDirectory() as tmp:
            pid_file = os.path.join(tmp, 'grandchild.pid')
            code = (
                'import subprocess, sys, time\n'
                f'child = subprocess.Popen([{_PY!r}, "-c", "import time; time.sleep(60)"])\n'
                f'open({pid_file!r}, "w").write(str(child.pid))\n'
                'time.sleep(60)\n'
            )
            cancel = threading.Event()

            def cancel_once_started() -> None:
                deadline = time.monotonic() + 10
                while not os.path.exists(pid_file) and time.monotonic() < deadline:
                    time.sleep(0.05)
                time.sleep(0.1)
                cancel.set()

            threading.Thread(target=cancel_once_started, daemon=True).start()
            with self.assertRaises(ProcessCancelled):
                _run(code, cancel=cancel)
            with open(pid_file, encoding='utf-8') as handle:
                grandchild = int(handle.read())
            deadline = time.monotonic() + 5
            while pid_alive(grandchild) and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertFalse(pid_alive(grandchild))


if __name__ == '__main__':
    unittest.main()
